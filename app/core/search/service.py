"""Run one search page: source and scope dispatch, rating scans, last-page maths.

Both interfaces call ``run_search``; the PySide6 ``SearchWorker`` only moves
it off the GUI thread, the Flet interface runs it with ``asyncio.to_thread``.

``client`` is the object with the Iwara and Oreno3D page methods (the
download manager in the app, a fake in tests).  ``should_stop`` is asked
between the pages of a rating scan; by default it follows the current
``CancelToken`` (or QThread interruption).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any

from ..net_policy import cancellation_requested
from ..oreno3d.search_query import (
    map_oreno3d_sort,
    parse_oreno3d_query,
    parse_oreno3d_tag_ids,
)
from ..rating import filter_by_rating
from ...i18n import tr
from .filtering import filter_videos, sort_videos
from .models import SearchFilters, SearchPageResult, SearchScope, SearchVideo
from .normalize import (
    normalize_author,
    normalize_image,
    normalize_oreno3d_listing,
    normalize_playlist,
    normalize_video,
)
from .query_params import (
    build_image_query_params,
    build_keyword_query_params,
    build_native_query_params,
    build_video_query_params,
    playlist_reference,
    profile_reference,
)

# ``/search`` ignores the SFW/NSFW rating, so with a rating chosen the search
# reads consecutive server pages until enough rows survive the local filter.
RATING_SCAN_MAX_PAGES = 4
RATING_SCAN_TARGET = 24


def run_search(
    filters: SearchFilters,
    scope: SearchScope,
    page: int,
    *,
    source: str,
    client: Any,
    should_stop: Callable[[], bool] = cancellation_requested,
) -> SearchPageResult:
    """One page of results; failures come back as ``SearchPageResult.error``."""

    return _SearchRun(filters, scope, page, source=source, client=client, should_stop=should_stop).run()


class _SearchRun:
    def __init__(self, filters, scope, page, *, source, client, should_stop):
        self.filters = filters
        self.scope = scope
        self.page = max(0, int(page))
        self.source = source
        self.client = client
        self.should_stop = should_stop

    def run(self) -> SearchPageResult:
        try:
            if self.source == "oreno3d" and self.scope in {"videos", "tags"}:
                return self._run_oreno3d_search()
            if self.scope == "authors":
                return self._run_author_search()
            if self.scope == "playlists":
                return self._run_playlist_search()
            if self.scope == "images":
                return self._run_native_search("images")
            return self._run_video_search()
        except Exception as exc:
            return SearchPageResult(
                scope=self.scope,
                error=str(exc),
                next_page=None,
                current_page=self.page,
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
            search_tags = getattr(self.client, "get_oreno3d_tag_search_page", None)
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
            listings, last_page = self.client.get_oreno3d_search_page(
                query.keyword,
                page=online_page,
                sort=sort,
                search_type=query.search_type or None,
                entity_id=query.entity_id or None,
            )
        videos = [normalize_oreno3d_listing(item) for item in listings]
        videos = [video for video in videos if video is not None]
        has_more = online_page < last_page
        return SearchPageResult(
            scope=self.scope,
            videos=videos,
            total=None,
            has_more=has_more,
            next_page=self.page + 1 if has_more else None,
            scanned_pages=1,
            current_page=self.page,
            last_page=max(0, last_page - 1),
        )

    def _scan_rated(self, scope: str, fetch_page, parse):
        """Collect rating-filtered rows from consecutive ``/search`` pages.

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
            if self.should_stop():
                break
            raw, _total, has_more, error = fetch_page(page)
            fetched = page
            videos.extend(parse(raw))
            if error or not has_more or len(videos) >= RATING_SCAN_TARGET:
                break
            page += 1
        return SearchPageResult(
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

    def _run_native_search(self, scope: str):
        """Read one page of Iwara's ``/search`` for images, users or playlists."""

        if scope == "images" and not self.filters.keyword.strip():
            return self._run_image_browse()
        if not self.filters.keyword.strip():
            messages = {
                "images": tr("Enter image keywords first", "请先输入图片关键词", "画像のキーワードを入力してください"),
                "authors": tr("Enter an author name first", "请先输入作者名称", "作者名を入力してください"),
                "playlists": tr("Enter playlist keywords first", "请先输入播放列表关键词", "プレイリストのキーワードを入力してください"),
            }
            return SearchPageResult(scope=scope, error=messages.get(scope, ""))
        if scope == "images" and self.filters.rating:
            def fetch_images(page: int):
                params = build_native_query_params(self.filters, scope, page)
                return self.client.get_search_native_page(
                    params["type"], params, page=page, limit=self.filters.page_size,
                )

            return self._scan_rated(
                scope,
                fetch_images,
                lambda raw: filter_by_rating(
                    [video for video in map(normalize_image, raw) if video is not None],
                    self.filters.rating,
                ),
            )
        params = build_native_query_params(self.filters, scope, self.page)
        raw_page, total, has_more, error = self.client.get_search_native_page(
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
        return result

    def _run_image_browse(self):
        """List image posts without a keyword, like the site's Images tab."""

        params = build_image_query_params(self.filters, self.page)
        raw_page, total, has_more, error = self.client.get_home_page(
            "image", params, page=self.page, limit=self.filters.page_size,
        )
        images = [video for video in map(normalize_image, raw_page) if video is not None]
        return SearchPageResult(
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

    def _run_author_search(self):
        keyword = (self.filters.keyword or self.filters.author).strip()
        username = profile_reference(keyword)
        if not username:
            # Free text uses Iwara's user index; /profile/<text> answers 404
            # for anything except an exact username.
            return self._run_native_search("authors")
        profile, error = self.client.get_search_user_profile(username)
        author = normalize_author(profile) if profile else None
        return SearchPageResult(
            scope="authors",
            authors=[author] if author else [],
            error=error if not author else "",
            scanned_pages=1,
        )

    def _run_playlist_search(self):
        playlist_id = playlist_reference(self.filters.keyword)
        if not playlist_id:
            return self._run_native_search("playlists")
        raw_videos = self.client.get_search_playlist_videos(playlist_id)
        videos = [normalize_video(raw) for raw in raw_videos]
        normalized = [video for video in videos if video is not None]
        playlist_filters = replace(self.filters, keyword="")
        filtered = sort_videos(filter_videos(normalized, playlist_filters), playlist_filters.sort)
        return SearchPageResult(
            scope="playlists",
            videos=filtered,
            total=len(filtered),
            has_more=False,
            scanned_pages=1,
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
                return self.client.get_search_keyword_page(
                    build_keyword_query_params(query_filters, page),
                    page=page,
                    limit=query_filters.page_size,
                )

            return self._scan_rated(
                self.scope,
                fetch_keyword,
                lambda raw: filter_videos(
                    [video for video in map(normalize_video, raw) if video is not None],
                    keyword_filters,
                ),
            )
        fetch = (
            self.client.get_search_keyword_page
            if keyword_search else self.client.get_search_video_page
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
        return SearchPageResult(
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
