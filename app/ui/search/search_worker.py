"""Fetch one page of search results off the GUI thread."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Signal

from ...core.oreno3d.search_query import (
    map_oreno3d_sort,
    parse_oreno3d_query,
    parse_oreno3d_tag_ids,
)
from ...core.search.models import SearchFilters, SearchPageResult, SearchScope, SearchVideo
from ...core.search.query_params import (
    build_image_query_params,
    build_keyword_query_params,
    build_native_query_params,
    build_video_query_params,
    playlist_reference,
    profile_reference,
)
from ...core.search.filtering import filter_videos, sort_videos
from ...core.search.normalize import (
    normalize_author,
    normalize_image,
    normalize_oreno3d_listing,
    normalize_playlist,
    normalize_video,
)
from ...core.rating import filter_by_rating
from ...i18n import tr
from ..worker_lifecycle import ManagedThread
from .page_dependencies import download_manager


# ``/search`` ignores the SFW/NSFW rating, so with a rating chosen the worker
# reads consecutive server pages until enough rows survive the local filter.
RATING_SCAN_MAX_PAGES = 4


RATING_SCAN_TARGET = 24


class SearchWorker(ManagedThread):
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

    def _scan_rated(self, scope: str, fetch_page, parse):
        """Collect rating-filtered rows from consecutive ``/search`` pages and emit them.

        ``fetch_page(page)`` returns ``(raw, total, has_more, error)`` and
        ``parse(raw)`` the already normalized and rating-filtered cards.  The
        next page continues after the last server page that was read, so no
        rows are skipped or repeated when Next is used.
        """

        videos: list[SearchVideo] = []
        error = ""
        has_more = False
        page = self.page
        fetched = self.page
        for _ in range(RATING_SCAN_MAX_PAGES):
            if self.isInterruptionRequested():
                break
            raw, _total, has_more, error = fetch_page(page)
            fetched = page
            videos.extend(parse(raw))
            if error or not has_more or len(videos) >= RATING_SCAN_TARGET:
                break
            page += 1
        self.result_ready.emit(
            SearchPageResult(
                scope=scope,
                videos=videos,
                total=None,
                has_more=has_more,
                next_page=fetched + 1 if has_more else None,
                scanned_pages=fetched - self.page + 1,
                error=error,
                current_page=self.page,
                last_page=None if has_more else fetched,
            )
        )

    def _run_native_search(self, scope: str):
        """Read one page of Iwara's ``/search`` for images, users or playlists."""

        if scope == "images" and not self.filters.keyword.strip():
            self._run_image_browse()
            return
        if not self.filters.keyword.strip():
            messages = {
                "images": tr("Enter image keywords first", "请先输入图片关键词", "画像のキーワードを入力してください"),
                "authors": tr("Enter an author name first", "请先输入作者名称", "作者名を入力してください"),
                "playlists": tr("Enter playlist keywords first", "请先输入播放列表关键词", "プレイリストのキーワードを入力してください"),
            }
            self.result_ready.emit(SearchPageResult(scope=scope, error=messages.get(scope, "")))
            return
        if scope == "images" and self.filters.rating:
            def fetch_images(page: int):
                params = build_native_query_params(self.filters, scope, page)
                return download_manager.get_search_native_page(
                    params["type"], params, page=page, limit=self.filters.page_size,
                )

            self._scan_rated(
                scope,
                fetch_images,
                lambda raw: filter_by_rating(
                    [video for video in map(normalize_image, raw) if video is not None],
                    self.filters.rating,
                ),
            )
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
            # /search ignores ``rating``; apply the SFW/NSFW choice here.
            result.videos = filter_by_rating(
                [video for video in map(normalize_image, raw_page) if video is not None],
                self.filters.rating,
            )
        elif scope == "authors":
            result.authors = [author for author in map(normalize_author, raw_page) if author is not None]
        else:
            result.playlists = [item for item in map(normalize_playlist, raw_page) if item is not None]
        self.result_ready.emit(result)

    def _run_image_browse(self):
        """List image posts without a keyword, like the site's Images tab."""

        params = build_image_query_params(self.filters, self.page)
        raw_page, total, has_more, error = download_manager.get_home_page(
            "image", params, page=self.page, limit=self.filters.page_size,
        )
        images = [video for video in map(normalize_image, raw_page) if video is not None]
        self.result_ready.emit(
            SearchPageResult(
                scope="images",
                videos=filter_by_rating(images, self.filters.rating),
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
        )

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
        if keyword_search and query_filters.rating:
            keyword_filters = replace(query_filters, keyword="")

            def fetch_keyword(page: int):
                return download_manager.get_search_keyword_page(
                    build_keyword_query_params(query_filters, page),
                    page=page,
                    limit=query_filters.page_size,
                )

            self._scan_rated(
                self.scope,
                fetch_keyword,
                lambda raw: filter_videos(
                    [video for video in map(normalize_video, raw) if video is not None],
                    keyword_filters,
                ),
            )
            return
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
