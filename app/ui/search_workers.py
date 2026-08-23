"""Background workers owned by the search interface."""
from __future__ import annotations

import re
import sys
import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from dataclasses import replace
from typing import Any

from PySide6.QtCore import QThread, Signal

from ..core.manager import download_manager as _default_download_manager
from ..core.oreno3d_search import (
    map_oreno3d_sort,
    parse_oreno3d_query,
    parse_oreno3d_tag_ids,
)
from ..core.search import (
    SearchFilters,
    SearchPageResult,
    SearchScope,
    SearchVideo,
    build_video_query_params,
    filter_videos,
    normalize_author,
    normalize_oreno3d_listing,
    normalize_video,
    sort_videos,
    split_search_terms,
)
from ..i18n import tr


class _SearchManagerProxy:
    """Resolve the page's manager lazily, preserving lightweight test fakes."""

    def __getattr__(self, name: str):
        page_module = sys.modules.get("app.ui.search_page")
        manager = getattr(page_module, "download_manager", _default_download_manager)
        return getattr(manager, name)


download_manager = _SearchManagerProxy()


DEFAULT_SEARCH_RESOLUTION_CONCURRENCY = 4
MAX_SEARCH_RESOLUTION_CONCURRENCY = 8
DEFAULT_COVER_DOWNLOAD_CONCURRENCY = 6
MAX_COVER_DOWNLOAD_CONCURRENCY = 16


def _extract_playlist_id(value: str) -> str:
    text = str(value or "").strip()
    match = re.search(r"/playlist/([A-Za-z0-9_-]+)", text)
    return match.group(1) if match else text


def _extract_iwara_video_id(value: str) -> str:
    match = re.search(r"/video/([^/?#]+)", str(value or ""))
    return match.group(1) if match else ""


def _resolve_oreno_video_id(video: SearchVideo, *, parallel: bool = True) -> str:
    """Resolve one Oreno card while remaining compatible with test fakes."""

    video_id = video.download_video_id or _extract_iwara_video_id(video.iwara_url)
    if video_id:
        return video_id
    source_id = video.video_id.removeprefix("oreno3d:")
    try:
        return str(
            download_manager.resolve_oreno3d_video_id(
                source_id,
                video.source_url,
                parallel=parallel,
            )
            or ""
        ).strip()
    except TypeError as exc:
        if "parallel" not in str(exc):
            raise
        return str(
            download_manager.resolve_oreno3d_video_id(source_id, video.source_url)
            or ""
        ).strip()


class SearchWorker(QThread):
    """Fetch and locally filter one result page off the GUI thread."""

    result_ready = Signal(object)

    def __init__(
        self,
        filters: SearchFilters,
        scope: SearchScope,
        page: int,
        generation: int,
        *,
        replace_results: bool,
        source: str,
    ):
        super().__init__()
        self.filters = filters
        self.scope = scope
        self.page = max(0, int(page))
        self.generation = generation
        self.replace_results = replace_results
        self.source = source

    def run(self):
        try:
            if self.source == "oreno3d" and self.scope in {"videos", "tags"}:
                self._run_oreno3d_search()
            elif self.scope == "authors":
                self._run_author_search()
            elif self.scope == "playlists":
                self._run_playlist_search()
            else:
                self._run_video_search()
        except Exception as exc:
            self.result_ready.emit(
                SearchPageResult(
                    scope=self.scope,
                    error=str(exc),
                    next_page=None,
                    current_page=self.page,
                )
            )

    def _run_oreno3d_search(self):
        """Forward one free-text or direct-tag page to Oreno3D."""

        sort = map_oreno3d_sort(self.filters.sort)
        online_page = self.page + 1
        tag_ids = (
            parse_oreno3d_tag_ids(self.filters.keyword)
            if self.scope == "tags"
            else ()
        )
        if len(tag_ids) > 1:
            search_tags = getattr(download_manager, "get_oreno3d_tag_search_page", None)
            if callable(search_tags):
                listings, last_page = search_tags(
                    tag_ids,
                    page=online_page,
                    sort=sort,
                )
            else:
                listings, last_page = [], 0
        else:
            query = parse_oreno3d_query(
                self.filters.keyword,
                scope="tags" if self.scope == "tags" else "videos",
            )
            listings, last_page = download_manager.get_oreno3d_search_page(
                query.keyword,
                page=online_page,
                sort=sort,
                search_type=query.search_type or None,
                entity_id=query.entity_id or None,
            )
        videos = [normalize_oreno3d_listing(item) for item in listings]
        videos = [video for video in videos if video is not None]
        has_more = online_page < last_page
        self.result_ready.emit(
            SearchPageResult(
                scope=self.scope,
                videos=videos,
                total=None,
                has_more=has_more,
                next_page=self.page + 1 if has_more else None,
                scanned_pages=1,
                current_page=self.page,
                last_page=max(0, last_page - 1),
            )
        )

    def _run_author_search(self):
        username = (self.filters.keyword or self.filters.author).strip()
        if not username:
            self.result_ready.emit(
                SearchPageResult(
                    scope="authors",
                    error=tr(
                        "Enter an author username first",
                        "请先输入作者用户名",
                        "作者ユーザー名を入力してください",
                    ),
                )
            )
            return
        profile, error = download_manager.get_search_user_profile(username)
        author = normalize_author(profile) if profile else None
        self.result_ready.emit(
            SearchPageResult(
                scope="authors",
                authors=[author] if author else [],
                error=error if not author else "",
                scanned_pages=1,
            )
        )

    def _run_playlist_search(self):
        playlist_id = _extract_playlist_id(self.filters.keyword)
        if not playlist_id:
            self.result_ready.emit(
                SearchPageResult(
                    scope="playlists",
                    error=tr(
                        "Enter a playlist ID or playlist URL first",
                        "请先输入播放列表 ID 或链接",
                        "プレイリストIDまたはURLを入力してください",
                    ),
                )
            )
            return
        raw_videos = download_manager.get_search_playlist_videos(playlist_id)
        videos = [normalize_video(raw) for raw in raw_videos]
        normalized = [video for video in videos if video is not None]
        playlist_filters = replace(self.filters, keyword="")
        filtered = sort_videos(filter_videos(normalized, playlist_filters), playlist_filters.sort)
        self.result_ready.emit(
            SearchPageResult(
                scope="playlists",
                videos=filtered,
                total=len(filtered),
                has_more=False,
                scanned_pages=1,
            )
        )

    def _run_video_search(self):
        query_filters = self.filters
        if self.scope == "tags":
            tag_terms = split_search_terms(self.filters.keyword)
            query_filters = replace(
                self.filters,
                keyword="",
                include_tags=tuple(dict.fromkeys((*self.filters.include_tags, *tag_terms))),
            )
        raw_page, total, has_more, error = download_manager.get_search_video_page(
            build_video_query_params(query_filters, self.page),
            page=self.page,
            limit=query_filters.page_size,
        )
        videos = [normalize_video(raw) for raw in raw_page]
        videos = [video for video in videos if video is not None]
        if self.scope == "tags":
            # The API has already applied the tag query. Its comma-separated
            # ``tag`` parameter is an OR-style remote filter, while the
            # generic local filter treats include_tags as an AND constraint.
            # Applying that generic filter here would turn a valid page into
            # an empty result set (the UI then shows e.g. 0 / 97).
            videos = sort_videos(videos, query_filters.sort)
        else:
            videos = sort_videos(filter_videos(videos, query_filters), query_filters.sort)
        self.result_ready.emit(
            SearchPageResult(
                scope=self.scope,
                videos=videos,
                total=total,
                has_more=has_more,
                next_page=self.page + 1 if has_more else None,
                scanned_pages=1,
                error=error,
                current_page=self.page,
                last_page=(
                    max(0, (total - 1) // query_filters.page_size)
                    if total is not None and total > 0
                    else self.page
                    if not has_more
                    else None
                ),
            )
        )


class SearchImageWorker(QThread):
    """Download visible card images through the manager's configured session."""

    image_ready = Signal(int, str, str, str)

    def __init__(
        self,
        generation: int,
        jobs: list[tuple[str, str, str]],
        *,
        concurrency: int = DEFAULT_COVER_DOWNLOAD_CONCURRENCY,
    ):
        super().__init__()
        self.generation = generation
        self.jobs = jobs
        self.concurrency = max(1, min(MAX_COVER_DOWNLOAD_CONCURRENCY, int(concurrency)))

    def run(self):
        thread_state = threading.local()
        clients: list[Any] = []
        clients_lock = threading.Lock()

        def fetch(job: tuple[str, str, str]):
            kind, item_key, image_url = job
            if self.isInterruptionRequested():
                return kind, item_key, ""
            client = getattr(thread_state, "api_client", None)
            if client is None:
                create_client = getattr(download_manager, "create_worker_api_client", None)
                if callable(create_client):
                    client = create_client()
                    thread_state.api_client = client
                    with clients_lock:
                        clients.append(client)
            try:
                path = download_manager.cache_search_image(
                    kind,
                    item_key,
                    image_url,
                    api_client=client,
                )
            except TypeError as exc:
                # Preserve compatibility with small manager fakes and older
                # extensions that still expose the three-argument cache method.
                if "api_client" not in str(exc):
                    raise
                path = download_manager.cache_search_image(kind, item_key, image_url)
            return kind, item_key, path

        executor = ThreadPoolExecutor(
            max_workers=min(self.concurrency, len(self.jobs)),
            thread_name_prefix="search-image",
        )
        try:
            futures = [executor.submit(fetch, job) for job in self.jobs]
            for future in as_completed(futures):
                if self.isInterruptionRequested():
                    break
                try:
                    kind, item_key, path = future.result()
                except Exception:
                    continue
                if path:
                    self.image_ready.emit(self.generation, kind, item_key, path)
        finally:
            executor.shutdown(wait=True, cancel_futures=True)
            close_client = getattr(download_manager, "close_worker_api_client", None)
            if callable(close_client):
                for client in clients:
                    close_client(client)


class SearchQueueResolveWorker(QThread):
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


class SearchOrenoLinkWorker(QThread):
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
        links: dict[str, dict[str, Any]] = {}
        errors: list[str] = []
        total = len(self.videos)
        self.progress.emit(0, total)
        if not self.videos:
            self.result_ready.emit(
                {"generation": self.generation, "links": links, "errors": errors}
            )
            return

        id_futures: dict[Any, SearchVideo] = {}
        metadata_futures: dict[Any, SearchVideo] = {}
        executor = ThreadPoolExecutor(
            max_workers=min(self.concurrency, len(self.videos)),
            thread_name_prefix="oreno-search-resolve",
        )
        completed = 0
        try:
            for video in self.videos:
                if video.source_kind != "oreno3d":
                    continue
                id_futures[executor.submit(_resolve_oreno_video_id, video)] = video

            while id_futures or metadata_futures:
                if self.isInterruptionRequested():
                    break
                done, _ = wait(
                    tuple(id_futures) + tuple(metadata_futures),
                    return_when=FIRST_COMPLETED,
                )
                for future in done:
                    video = id_futures.pop(future, None)
                    if video is not None:
                        completed += 1
                        try:
                            video_id = str(future.result() or "").strip()
                        except Exception as exc:
                            video_id = ""
                            errors.append(f"{video.title}: {exc}")
                        self.progress.emit(completed, total)
                        if not video_id:
                            errors.append(
                                f"{video.title}: Oreno3D detail did not expose an Iwara video ID"
                            )
                            continue

                        link = {
                            "id": video_id,
                            "url": f"https://www.iwara.tv/video/{video_id}",
                            "metadata": {},
                        }
                        links[video.video_id] = link
                        self.item_ready.emit(
                            {
                                "generation": self.generation,
                                "stage": "id",
                                "link": dict(link),
                                "video_id": video.video_id,
                            }
                        )
                        if self.hydrate_metadata:
                            metadata_futures[
                                executor.submit(
                                    download_manager.get_iwara_video_info,
                                    video_id,
                                )
                            ] = video
                        continue

                    video = metadata_futures.pop(future, None)
                    if video is None:
                        continue
                    link = links.get(video.video_id)
                    if link is None:
                        continue
                    try:
                        metadata, metadata_error = future.result()
                    except Exception as exc:
                        metadata, metadata_error = {}, str(exc)
                    if metadata_error:
                        errors.append(f"{video.title}: {metadata_error}")
                    link["metadata"] = metadata if isinstance(metadata, dict) else {}
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
        finally:
            executor.shutdown(
                wait=not self.isInterruptionRequested(),
                cancel_futures=True,
            )
        self.result_ready.emit(
            {"generation": self.generation, "links": links, "errors": errors}
        )


class SearchIwaraAuthorWorker(QThread):
    """Hydrate one Iwara video's metadata before author subscription."""

    result_ready = Signal(object)

    def __init__(self, generation: int, video_id: str):
        super().__init__()
        self.generation = generation
        self.video_id = str(video_id or "").strip()

    def run(self):
        metadata: dict[str, Any] = {}
        error = ""
        try:
            value, error = download_manager.get_iwara_video_info(self.video_id)
            if isinstance(value, dict):
                metadata = dict(value)
        except Exception as exc:
            error = str(exc)
        self.result_ready.emit(
            {
                "generation": self.generation,
                "video_id": self.video_id,
                "metadata": metadata,
                "error": str(error or ""),
            }
        )


class SearchOrenoAuthorWorker(QThread):
    """Resolve an Oreno3D author page and map it to an Iwara profile."""

    result_ready = Signal(object)

    def __init__(self, generation: int, video: SearchVideo, *, max_videos: int = 8):
        super().__init__()
        self.generation = generation
        self.video = video
        self.video_id = str(video.video_id or "").strip()
        self.max_videos = max(1, min(20, int(max_videos)))

    def run(self):
        result: dict[str, Any] = {}
        error = ""
        try:
            resolver = getattr(download_manager, "resolve_oreno3d_author", None)
            if not callable(resolver):
                raise RuntimeError("Oreno3D author resolver is unavailable")
            raw = self.video.raw if isinstance(self.video.raw, dict) else {}
            source_id = self.video_id.removeprefix("oreno3d:")
            kwargs = {
                "author_url": str(raw.get("oreno3d_author_url") or "").strip(),
                "author_name": str(raw.get("oreno3d_author_name") or self.video.author_name or "").strip(),
                "max_videos": self.max_videos,
                "parallel": True,
            }
            try:
                value = resolver(source_id, self.video.source_url, **kwargs)
            except TypeError as exc:
                # Keep older integrations/fakes usable while the manager API
                # rolls out the durable-author parameters.
                if not any(name in str(exc) for name in ("author_url", "author_name", "max_videos", "parallel")):
                    raise
                value = resolver(source_id, self.video.source_url)
            if isinstance(value, dict):
                result = dict(value)
            else:
                error = "Oreno3D author resolver returned no result"
        except Exception as exc:
            error = str(exc)
        self.result_ready.emit(
            {
                "generation": self.generation,
                "video_id": self.video_id,
                "result": result,
                "error": error,
            }
        )


