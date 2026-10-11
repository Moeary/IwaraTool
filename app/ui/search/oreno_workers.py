"""Resolve Oreno3D cards to Iwara videos in the background."""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from typing import Any

from PySide6.QtCore import Signal

from ...core.search.models import SearchVideo
from ...i18n import tr
from ..worker_lifecycle import ManagedThread
from .page_dependencies import download_manager
from .result_authors import _extract_iwara_video_id
from .worker_limits import DEFAULT_SEARCH_RESOLUTION_CONCURRENCY, MAX_SEARCH_RESOLUTION_CONCURRENCY


def _resolve_oreno_video_id(video: SearchVideo, *, parallel: bool = True) -> str:
    """Resolve one Oreno card while remaining compatible with test fakes."""

    video_id = video.download_video_id or _extract_iwara_video_id(video.iwara_url)
    if video_id:
        return video_id
    source_id = video.video_id.removeprefix("oreno3d:")
    source_url = str(video.raw.get("oreno3d_url") or video.source_url or "")
    try:
        return str(
            download_manager.resolve_oreno3d_video_id(
                source_id,
                source_url,
                parallel=parallel,
            )
            or ""
        ).strip()
    except TypeError as exc:
        if "parallel" not in str(exc):
            raise
        return str(
            download_manager.resolve_oreno3d_video_id(source_id, source_url)
            or ""
        ).strip()


def _resolve_oreno_video_details(video: SearchVideo) -> dict[str, Any]:
    """Keep source author information independently of Iwara availability."""

    resolver = getattr(download_manager, "resolve_oreno3d_video_details", None)
    if callable(resolver):
        source_url = str(video.raw.get("oreno3d_url") or video.source_url or "")
        result = resolver(video.video_id.removeprefix("oreno3d:"), source_url, parallel=True)
        if isinstance(result, dict):
            return result
    return {"video_id": _resolve_oreno_video_id(video)}


class SearchQueueResolveWorker(ManagedThread):
    """Resolve only the selected Oreno3D cards before queueing them."""

    result_ready = Signal(object)

    def __init__(
        self,
        videos: list[SearchVideo],
        *,
        concurrency: int = DEFAULT_SEARCH_RESOLUTION_CONCURRENCY,
    ):
        super().__init__()
        self.videos = videos
        self.concurrency = max(1, min(MAX_SEARCH_RESOLUTION_CONCURRENCY, int(concurrency)))

    def run(self):
        ids: list[str] = []
        skipped = 0
        errors: list[str] = []
        pending: list[SearchVideo] = []
        for video in self.videos:
            if video.source_kind == "iwara":
                video_id = video.download_video_id or video.video_id
                if video_id:
                    ids.append(video_id)
                else:
                    skipped += 1
            elif video.source_kind == "oreno3d":
                pending.append(video)
            else:
                skipped += 1

        if pending:
            executor = ThreadPoolExecutor(
                max_workers=min(self.concurrency, len(pending)),
                thread_name_prefix="oreno-queue-resolve",
            )
            try:
                futures = {
                    executor.submit(_resolve_oreno_video_id, video): video
                    for video in pending
                }
                for future in as_completed(futures):
                    video = futures[future]
                    if self.isInterruptionRequested():
                        break
                    try:
                        video_id = future.result()
                    except Exception as exc:
                        video_id = ""
                        errors.append(f"{video.title}: {exc}")
                    if video_id:
                        ids.append(video_id)
                    else:
                        skipped += 1
            finally:
                executor.shutdown(
                    wait=not self.isInterruptionRequested(),
                    cancel_futures=True,
                )
        self.result_ready.emit({"ids": ids, "skipped": skipped, "errors": errors})


class SearchOrenoLinkWorker(ManagedThread):
    """Resolve Oreno IDs, then hydrate each result from the Iwara API."""

    item_ready = Signal(object)
    result_ready = Signal(object)
    progress = Signal(int, int)

    def __init__(
        self,
        generation: int,
        videos: list[SearchVideo],
        *,
        concurrency: int = DEFAULT_SEARCH_RESOLUTION_CONCURRENCY,
        hydrate_metadata: bool = True,
    ):
        super().__init__()
        self.generation = generation
        self.videos = videos
        self.concurrency = max(1, min(MAX_SEARCH_RESOLUTION_CONCURRENCY, int(concurrency)))
        self.hydrate_metadata = bool(hydrate_metadata)

    def run(self):
        self._links: dict[str, dict[str, Any]] = {}
        self._errors: list[str] = []
        self._total = len(self.videos)
        self._completed = 0
        self.progress.emit(0, self._total)
        if not self.videos:
            self._emit_result()
            return

        self._id_futures: dict[Any, SearchVideo] = {}
        self._metadata_futures: dict[Any, SearchVideo] = {}
        self._author_futures: dict[Any, list[SearchVideo]] = {}
        self._author_jobs: dict[str, Any] = {}
        self._author_results: dict[str, dict[str, Any]] = {}
        self._executor = ThreadPoolExecutor(
            max_workers=min(self.concurrency, len(self.videos)),
            thread_name_prefix="oreno-search-resolve",
        )
        try:
            for video in self.videos:
                if video.source_kind != "oreno3d" and not video.raw.get("oreno3d_url"):
                    continue
                self._id_futures[self._executor.submit(_resolve_oreno_video_details, video)] = video

            while self._id_futures or self._metadata_futures or self._author_futures:
                if self.isInterruptionRequested():
                    break
                done, _ = wait(
                    tuple(self._id_futures) + tuple(self._metadata_futures) + tuple(self._author_futures),
                    return_when=FIRST_COMPLETED,
                )
                for future in done:
                    self._handle_done(future)
        finally:
            self._executor.shutdown(
                wait=not self.isInterruptionRequested(),
                cancel_futures=True,
            )
        self._emit_result()

    def _emit_result(self):
        self.result_ready.emit(
            {"generation": self.generation, "links": self._links, "errors": self._errors}
        )

    def _handle_done(self, future):
        video = self._id_futures.pop(future, None)
        if video is not None:
            self._on_id_resolved(future, video)
            return
        author_videos = self._author_futures.pop(future, None)
        if author_videos is not None:
            self._on_author_resolved(future, author_videos)
            return
        video = self._metadata_futures.pop(future, None)
        if video is not None:
            self._on_metadata(future, video)

    def _on_id_resolved(self, future, video: SearchVideo):
        self._completed += 1
        error = ""
        try:
            detail = future.result()
        except Exception as exc:
            detail = {}
            error = str(exc)
        video_id = str(detail.get("video_id") or "").strip()
        self.progress.emit(self._completed, self._total)
        if not video_id:
            error = error or tr(
                "The Oreno3D page has no supported Iwara video link",
                "Oreno3D 页面未提供可识别的 Iwara 视频链接",
                "Oreno3Dページに対応するIwara動画リンクがありません",
            )
            self._errors.append(f"{video.title}: {error}")

        link = {
            "id": video_id,
            "url": f"https://www.iwara.tv/video/{video_id}" if video_id else "",
            "metadata": {},
            "error": error,
            **{key: detail.get(key, "") for key in (
                "oreno_author_id", "oreno_author_name", "oreno_author_url",
            )},
        }
        self._links[video.video_id] = link
        self.item_ready.emit(
            {
                "generation": self.generation,
                "stage": "id" if video_id else "source",
                "link": dict(link),
                "video_id": video.video_id,
            }
        )
        if self.hydrate_metadata and video_id:
            self._metadata_futures[
                self._executor.submit(
                    download_manager.get_iwara_video_info,
                    video_id,
                )
            ] = video
        elif not video_id:
            self._find_author(video)

    def _on_author_resolved(self, future, author_videos: list[SearchVideo]):
        try:
            result = future.result()
            result = result if isinstance(result, dict) else {}
        except Exception as exc:
            result = {"error": str(exc)}
        author_url = str(self._links[author_videos[0].video_id].get("oreno_author_url") or "")
        self._author_results[author_url] = result
        for author_video in author_videos:
            self._publish_author(author_video, result)

    def _on_metadata(self, future, video: SearchVideo):
        link = self._links.get(video.video_id)
        if link is None:
            return
        try:
            metadata, metadata_error = future.result()
        except Exception as exc:
            metadata, metadata_error = {}, str(exc)
        if metadata_error:
            self._errors.append(f"{video.title}: {metadata_error}")
        link["metadata"] = metadata if isinstance(metadata, dict) else {}
        link["metadata_error"] = str(metadata_error or "")
        self.item_ready.emit(
            {
                "generation": self.generation,
                "stage": "metadata",
                "link": {
                    **link,
                    "metadata": dict(link["metadata"]),
                },
                "video_id": video.video_id,
            }
        )
        user = link["metadata"].get("user") or {}
        if not isinstance(user, dict) or not (user.get("username") or user.get("slug")):
            self._find_author(video)

    def _publish_author(self, video: SearchVideo, result: dict[str, Any]):
        link = self._links[video.video_id]
        link["iwara_author"] = result.get("iwara_author") or {}
        link["author_error"] = str(result.get("error") or "")
        self.item_ready.emit({
            "generation": self.generation, "stage": "author",
            "video_id": video.video_id, "link": dict(link),
        })

    def _find_author(self, video: SearchVideo):
        # Resolve a deleted/private work's author through other works.
        # Share each author lookup within the page and use the same pool,
        # so a page of dead videos cannot create unbounded workers.
        if not self.hydrate_metadata or self.isInterruptionRequested():
            return
        link = self._links[video.video_id]
        author_url = str(link.get("oreno_author_url") or "")
        resolver = getattr(download_manager, "resolve_oreno3d_author", None)
        if not author_url or not callable(resolver):
            return
        if author_url in self._author_results:
            self._publish_author(video, self._author_results[author_url])
        elif author_url in self._author_jobs:
            self._author_futures[self._author_jobs[author_url]].append(video)
        else:
            future = self._executor.submit(
                resolver, author_url=author_url,
                author_name=str(link.get("oreno_author_name") or ""),
                max_videos=8, parallel=True,
            )
            self._author_jobs[author_url] = future
            self._author_futures[future] = [video]
