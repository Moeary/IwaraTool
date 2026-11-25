"""Resolve Oreno3D search cards to Iwara videos, metadata and authors.

The work behind the PySide6 ``SearchQueueResolveWorker`` and
``SearchOrenoLinkWorker``; the Flet interface runs the same functions with
``asyncio.to_thread``.  Each resolution makes several blocking requests, so
the functions fan out over a bounded thread pool.

``manager`` is the download manager (passed in so tests can replace it).
``should_stop`` is asked between completions; once it is true no new work is
started and the pool is shut down without waiting for running requests.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from typing import Any

from ...i18n import tr
from .. import site as sites
from ..net_policy import cancellation_requested
from .limits import DEFAULT_SEARCH_RESOLUTION_CONCURRENCY, resolution_concurrency
from .models import SearchVideo
from .query_params import video_reference


def oreno_source(video: SearchVideo) -> tuple[str, str]:
    """``(Oreno3D id, Oreno3D page URL)`` of a card."""

    return (
        video.video_id.removeprefix("oreno3d:"),
        str(video.raw.get("oreno3d_url") or video.source_url or ""),
    )


def resolve_oreno_video_id(manager: Any, video: SearchVideo, *, parallel: bool = True) -> str:
    """The Iwara video ID an Oreno3D card links to (``""`` when there is none)."""

    video_id = video.download_video_id or video_reference(video.iwara_url)
    if video_id:
        return video_id
    source_id, source_url = oreno_source(video)
    return str(manager.resolve_oreno3d_video_id(source_id, source_url, parallel=parallel) or "").strip()


def resolve_oreno_video_details(manager: Any, video: SearchVideo) -> dict[str, Any]:
    """Iwara video ID plus the Oreno3D author, kept even when Iwara has no video."""

    resolver = getattr(manager, "resolve_oreno3d_video_details", None)
    if callable(resolver):
        result = resolver(*oreno_source(video), parallel=True)
        if isinstance(result, dict):
            return result
    return {"video_id": resolve_oreno_video_id(manager, video)}


def resolve_queue_ids(
    manager: Any,
    videos: list[SearchVideo],
    *,
    concurrency: int = DEFAULT_SEARCH_RESOLUTION_CONCURRENCY,
    should_stop: Callable[[], bool] = cancellation_requested,
) -> dict[str, Any]:
    """``{"ids", "skipped", "errors"}``: the Iwara IDs to queue for selected cards.

    Iwara cards are taken as they are; Oreno3D cards are resolved in parallel.
    """

    ids: list[str] = []
    skipped = 0
    errors: list[str] = []
    pending: list[SearchVideo] = []
    for video in videos:
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
            max_workers=min(resolution_concurrency(concurrency), len(pending)),
            thread_name_prefix="oreno-queue-resolve",
        )
        try:
            futures = {executor.submit(resolve_oreno_video_id, manager, video): video for video in pending}
            for future in as_completed(futures):
                video = futures[future]
                if should_stop():
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
            executor.shutdown(wait=not should_stop(), cancel_futures=True)
    return {"ids": ids, "skipped": skipped, "errors": errors}


class OrenoLinkResolver:
    """Resolve each Oreno3D card's Iwara ID, then its metadata, then (if needed) its author.

    ``on_item(event)`` receives ``{"generation", "stage", "video_id", "link"}``
    as each stage finishes (stage ``id``/``source``, ``metadata``, ``author``);
    ``on_progress(done, total)`` counts resolved IDs.  ``run()`` returns
    ``{"generation", "links", "errors"}``.

    A card whose Iwara video is gone, private or has no uploader in its metadata
    gets its author through the author's other works; lookups are shared per
    author within the page and run in the same pool, so a page of dead videos
    cannot start unbounded work.
    """

    def __init__(
        self,
        manager: Any,
        generation: int,
        videos: list[SearchVideo],
        *,
        concurrency: int = DEFAULT_SEARCH_RESOLUTION_CONCURRENCY,
        hydrate_metadata: bool = True,
        on_item: Callable[[dict[str, Any]], None] = lambda _event: None,
        on_progress: Callable[[int, int], None] = lambda _done, _total: None,
        should_stop: Callable[[], bool] = cancellation_requested,
    ):
        self.manager = manager
        self.generation = generation
        self.videos = videos
        self.concurrency = resolution_concurrency(concurrency)
        self.hydrate_metadata = bool(hydrate_metadata)
        self.on_item = on_item
        self.on_progress = on_progress
        self.should_stop = should_stop

    def run(self) -> dict[str, Any]:
        self._links: dict[str, dict[str, Any]] = {}
        self._errors: list[str] = []
        self._total = len(self.videos)
        self._completed = 0
        self.on_progress(0, self._total)
        if not self.videos:
            return self._result()

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
                future = self._executor.submit(resolve_oreno_video_details, self.manager, video)
                self._id_futures[future] = video

            while self._id_futures or self._metadata_futures or self._author_futures:
                if self.should_stop():
                    break
                done, _ = wait(
                    tuple(self._id_futures) + tuple(self._metadata_futures) + tuple(self._author_futures),
                    return_when=FIRST_COMPLETED,
                )
                for future in done:
                    self._handle_done(future)
        finally:
            self._executor.shutdown(wait=not self.should_stop(), cancel_futures=True)
        return self._result()

    def _result(self) -> dict[str, Any]:
        return {"generation": self.generation, "links": self._links, "errors": self._errors}

    def _emit(self, stage: str, video: SearchVideo, link: dict[str, Any]) -> None:
        self.on_item({"generation": self.generation, "stage": stage, "video_id": video.video_id, "link": link})

    def _handle_done(self, future) -> None:
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

    def _on_id_resolved(self, future, video: SearchVideo) -> None:
        self._completed += 1
        error = ""
        try:
            detail = future.result()
        except Exception as exc:
            detail = {}
            error = str(exc)
        video_id = str(detail.get("video_id") or "").strip()
        self.on_progress(self._completed, self._total)
        if not video_id:
            error = error or tr(
                "The Oreno3D page has no supported Iwara video link",
                "Oreno3D 页面未提供可识别的 Iwara 视频链接",
                "Oreno3Dページに対応するIwara動画リンクがありません",
            )
            self._errors.append(f"{video.title}: {error}")

        link = {
            "id": video_id,
            "url": sites.video_url(video_id, sites.SITE_TV),
            "metadata": {},
            "error": error,
            **{key: detail.get(key, "") for key in ("oreno_author_id", "oreno_author_name", "oreno_author_url")},
        }
        self._links[video.video_id] = link
        self._emit("id" if video_id else "source", video, dict(link))
        if self.hydrate_metadata and video_id:
            future = self._executor.submit(self.manager.get_iwara_video_info, video_id)
            self._metadata_futures[future] = video
        elif not video_id:
            self._find_author(video)

    def _on_author_resolved(self, future, author_videos: list[SearchVideo]) -> None:
        try:
            result = future.result()
            result = result if isinstance(result, dict) else {}
        except Exception as exc:
            result = {"error": str(exc)}
        author_url = str(self._links[author_videos[0].video_id].get("oreno_author_url") or "")
        self._author_results[author_url] = result
        for author_video in author_videos:
            self._publish_author(author_video, result)

    def _on_metadata(self, future, video: SearchVideo) -> None:
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
        self._emit("metadata", video, {**link, "metadata": dict(link["metadata"])})
        user = link["metadata"].get("user") or {}
        if not isinstance(user, dict) or not (user.get("username") or user.get("slug")):
            self._find_author(video)

    def _publish_author(self, video: SearchVideo, result: dict[str, Any]) -> None:
        link = self._links[video.video_id]
        link["iwara_author"] = result.get("iwara_author") or {}
        link["author_error"] = str(result.get("error") or "")
        self._emit("author", video, dict(link))

    def _find_author(self, video: SearchVideo) -> None:
        if not self.hydrate_metadata or self.should_stop():
            return
        link = self._links[video.video_id]
        author_url = str(link.get("oreno_author_url") or "")
        resolver = getattr(self.manager, "resolve_oreno3d_author", None)
        if not author_url or not callable(resolver):
            return
        if author_url in self._author_results:
            self._publish_author(video, self._author_results[author_url])
        elif author_url in self._author_jobs:
            self._author_futures[self._author_jobs[author_url]].append(video)
        else:
            future = self._executor.submit(
                resolver,
                author_url=author_url,
                author_name=str(link.get("oreno_author_name") or ""),
                max_videos=8,
                parallel=True,
            )
            self._author_jobs[author_url] = future
            self._author_futures[future] = [video]
