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
    SearchAuthor,
    SearchFilters,
    SearchPageResult,
    SearchScope,
    SearchVideo,
    build_keyword_query_params,
    build_native_query_params,
    build_video_query_params,
    filter_videos,
    normalize_author,
    normalize_image,
    normalize_oreno3d_listing,
    normalize_playlist,
    normalize_video,
    playlist_reference,
    profile_reference,
    sort_videos,
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


def _extract_iwara_video_id(value: str) -> str:
    match = re.search(r"/video/([^/?#]+)", str(value or ""))
    return match.group(1) if match else ""


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
            elif self.scope == "images":
                self._run_native_search("images")
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

    def _run_native_search(self, scope: str):
        """Read one page of Iwara's ``/search`` for images, users or playlists."""

        if not self.filters.keyword.strip():
            messages = {
                "images": tr("Enter image keywords first", "请先输入图片关键词", "画像のキーワードを入力してください"),
                "authors": tr("Enter an author name first", "请先输入作者名称", "作者名を入力してください"),
                "playlists": tr("Enter playlist keywords first", "请先输入播放列表关键词", "プレイリストのキーワードを入力してください"),
            }
            self.result_ready.emit(SearchPageResult(scope=scope, error=messages.get(scope, "")))
            return
        params = build_native_query_params(self.filters, scope, self.page)
        raw_page, total, has_more, error = download_manager.get_search_native_page(
            params["type"],
            params,
            page=self.page,
            limit=self.filters.page_size,
        )
        result = SearchPageResult(
            scope=scope,
            total=total,
            has_more=has_more,
            next_page=self.page + 1 if has_more else None,
            scanned_pages=1,
            error=error,
            current_page=self.page,
            last_page=(
                max(0, (total - 1) // self.filters.page_size)
                if total is not None and total > 0
                else self.page
                if not has_more
                else None
            ),
        )
        if scope == "images":
            result.videos = [video for video in map(normalize_image, raw_page) if video is not None]
        elif scope == "authors":
            result.authors = [author for author in map(normalize_author, raw_page) if author is not None]
        else:
            result.playlists = [item for item in map(normalize_playlist, raw_page) if item is not None]
        self.result_ready.emit(result)

    def _run_author_search(self):
        keyword = (self.filters.keyword or self.filters.author).strip()
        username = profile_reference(keyword)
        if not username:
            # Free text uses Iwara's user index; /profile/<text> answers 404
            # for anything except an exact username.
            self._run_native_search("authors")
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
        playlist_id = playlist_reference(self.filters.keyword)
        if not playlist_id:
            self._run_native_search("playlists")
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
        keyword_search = self.scope == "videos" and bool(self.filters.keyword.strip())
        if self.scope == "tags":
            query_filters = replace(
                self.filters,
                keyword="",
                include_tags=(*self.filters.include_tags, self.filters.keyword),
            )
        fetch = (
            download_manager.get_search_keyword_page
            if keyword_search else download_manager.get_search_video_page
        )
        params = (
            build_keyword_query_params(query_filters, self.page)
            if keyword_search else build_video_query_params(query_filters, self.page)
        )
        raw_page, total, has_more, error = fetch(
            params,
            page=self.page,
            limit=query_filters.page_size,
        )
        videos = [normalize_video(raw) for raw in raw_page]
        videos = [video for video in videos if video is not None]
        # Remote keyword matches can live in descriptions or use search
        # syntax. Substring matching would discard valid hits. Likewise tags
        # have already been applied remotely. Preserve the server's order;
        # sorting one page locally cannot implement a global search order.
        local_filters = replace(
            query_filters, keyword="",
            include_tags=() if self.scope == "tags" else query_filters.include_tags,
        )
        videos = filter_videos(videos, local_filters)
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
        author_futures: dict[Any, list[SearchVideo]] = {}
        author_jobs: dict[str, Any] = {}
        author_results: dict[str, dict[str, Any]] = {}
        executor = ThreadPoolExecutor(
            max_workers=min(self.concurrency, len(self.videos)),
            thread_name_prefix="oreno-search-resolve",
        )
        completed = 0

        def publish_author(video: SearchVideo, result: dict[str, Any]):
            link = links[video.video_id]
            link["iwara_author"] = result.get("iwara_author") or {}
            link["author_error"] = str(result.get("error") or "")
            self.item_ready.emit({
                "generation": self.generation, "stage": "author",
                "video_id": video.video_id, "link": dict(link),
            })

        def find_author(video: SearchVideo):
            # Resolve a deleted/private work's author through other works.
            # Share each author lookup within the page and use the same pool,
            # so a page of dead videos cannot create unbounded workers.
            if not self.hydrate_metadata or self.isInterruptionRequested():
                return
            link = links[video.video_id]
            author_url = str(link.get("oreno_author_url") or "")
            resolver = getattr(download_manager, "resolve_oreno3d_author", None)
            if not author_url or not callable(resolver):
                return
            if author_url in author_results:
                publish_author(video, author_results[author_url])
            elif author_url in author_jobs:
                author_futures[author_jobs[author_url]].append(video)
            else:
                future = executor.submit(
                    resolver, author_url=author_url,
                    author_name=str(link.get("oreno_author_name") or ""),
                    max_videos=8, parallel=True,
                )
                author_jobs[author_url] = future
                author_futures[future] = [video]

        try:
            for video in self.videos:
                if video.source_kind != "oreno3d" and not video.raw.get("oreno3d_url"):
                    continue
                id_futures[executor.submit(_resolve_oreno_video_details, video)] = video

            while id_futures or metadata_futures or author_futures:
                if self.isInterruptionRequested():
                    break
                done, _ = wait(
                    tuple(id_futures) + tuple(metadata_futures) + tuple(author_futures),
                    return_when=FIRST_COMPLETED,
                )
                for future in done:
                    video = id_futures.pop(future, None)
                    if video is not None:
                        completed += 1
                        error = ""
                        try:
                            detail = future.result()
                        except Exception as exc:
                            detail = {}
                            error = str(exc)
                        video_id = str(detail.get("video_id") or "").strip()
                        self.progress.emit(completed, total)
                        if not video_id:
                            error = error or tr(
                                "The Oreno3D page has no supported Iwara video link",
                                "Oreno3D 页面未提供可识别的 Iwara 视频链接",
                                "Oreno3Dページに対応するIwara動画リンクがありません",
                            )
                            errors.append(f"{video.title}: {error}")

                        link = {
                            "id": video_id,
                            "url": f"https://www.iwara.tv/video/{video_id}" if video_id else "",
                            "metadata": {},
                            "error": error,
                            **{key: detail.get(key, "") for key in (
                                "oreno_author_id", "oreno_author_name", "oreno_author_url",
                            )},
                        }
                        links[video.video_id] = link
                        self.item_ready.emit(
                            {
                                "generation": self.generation,
                                "stage": "id" if video_id else "source",
                                "link": dict(link),
                                "video_id": video.video_id,
                            }
                        )
                        if self.hydrate_metadata and video_id:
                            metadata_futures[
                                executor.submit(
                                    download_manager.get_iwara_video_info,
                                    video_id,
                                )
                            ] = video
                        elif not video_id:
                            find_author(video)
                        continue

                    author_videos = author_futures.pop(future, None)
                    if author_videos is not None:
                        try:
                            result = future.result()
                            result = result if isinstance(result, dict) else {}
                        except Exception as exc:
                            result = {"error": str(exc)}
                        author_url = str(links[author_videos[0].video_id].get("oreno_author_url") or "")
                        author_results[author_url] = result
                        for author_video in author_videos:
                            publish_author(author_video, result)
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
                        find_author(video)
        finally:
            executor.shutdown(
                wait=not self.isInterruptionRequested(),
                cancel_futures=True,
            )
        self.result_ready.emit(
            {"generation": self.generation, "links": links, "errors": errors}
        )


class SearchIwaraAuthorWorker(QThread):
    """Hydrate one Iwara video's metadata before an author action."""

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


class SearchAuthorProfileWorker(QThread):
    """Resolve a known Iwara account to its real user ID off the UI thread."""

    result_ready = Signal(object)

    def __init__(
        self,
        generation: int,
        action_generation: int,
        target: tuple[str, str, str, str],
    ):
        super().__init__()
        self.generation = generation
        self.action_generation = action_generation
        self.target = target

    def run(self):
        author: SearchAuthor | None = None
        error = ""
        try:
            profile, error = download_manager.get_search_user_profile(self.target[0])
            user = profile.get("user") if isinstance(profile, dict) else None
            # normalize_author historically falls back to the username for
            # card identity. That fallback cannot be sent as a /videos user ID.
            if isinstance(user, dict) and str(user.get("id") or user.get("userId") or user.get("user_id") or "").strip():
                author = normalize_author(profile)
            if author is None and not error:
                error = tr(
                    "The Iwara profile did not contain a user ID",
                    "Iwara 作者资料未包含用户 ID",
                    "Iwara作者プロフィールにユーザーIDがありません",
                )
        except Exception as exc:
            error = str(exc)
        if self.isInterruptionRequested():
            return
        self.result_ready.emit(
            {
                "generation": self.generation,
                "action_generation": self.action_generation,
                "author": author,
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
            source_url = str(raw.get("oreno3d_url") or self.video.source_url or "").strip()
            kwargs = {
                "author_url": str(raw.get("oreno3d_author_url") or "").strip(),
                "author_name": str(raw.get("oreno3d_author_name") or self.video.author_name or "").strip(),
                "max_videos": self.max_videos,
                "parallel": True,
            }
            iwara_id = self.video.download_video_id or _extract_iwara_video_id(self.video.iwara_url)
            if iwara_id:
                kwargs["iwara_video_id"] = iwara_id
            if isinstance(raw.get("oreno_iwara_author"), dict):
                kwargs["iwara_author"] = dict(raw["oreno_iwara_author"])
            try:
                value = resolver(source_id, source_url, **kwargs)
            except TypeError as exc:
                # Keep older integrations/fakes usable while the manager API
                # rolls out the durable-author parameters.
                if not any(name in str(exc) for name in ("author_url", "author_name", "max_videos", "parallel", "iwara_author", "iwara_video_id")):
                    raise
                value = resolver(source_id, source_url)
            if isinstance(value, dict):
                result = dict(value)
                error = str(result.get("error") or "")
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


