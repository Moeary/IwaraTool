"""Paged listings and search: videos, images, playlists and query results."""

from __future__ import annotations

from typing import Any

from ...i18n import tr
from .endpoints import BASE_API, IWARA_SEARCH_SORTS
from .errors import _extract_api_message


class ListingApiMixin:
    """Paged listings and search: videos, images, playlists and query results."""

    def get_playlist_videos(
        self, playlist_id: str, max_pages: int = 100
    ) -> list[dict]:
        """Fetch all video stubs in a playlist (paginated)."""
        videos: list[dict] = []
        for page in range(max_pages + 1):
            try:
                data = self._get_json(
                    f"{BASE_API}/playlist/{playlist_id}",
                    params={"page": page},
                )
                results: list[dict] = data.get("results", [])
                if not results:
                    break
                videos.extend(results)
            except Exception:
                break
        return videos

    def get_videos_page(
        self,
        query_params: dict[str, Any] | None = None,
        *,
        page: int = 0,
        limit: int = 32,
    ) -> tuple[list[dict], int | None, bool, str]:
        """Browse videos by tags, author or sort; this is not text search."""

        params = dict(query_params or {})
        # Current /videos applies plural `tags`; singular `tag` is ignored.
        # Accept old caller input, but always send the verified API parameter.
        legacy_tag = params.pop("tag", "")
        if legacy_tag and not params.get("tags"):
            params["tags"] = legacy_tag
        return self._get_video_result_page("/videos", params, page=page, limit=limit)

    def get_images_page(
        self,
        query_params: dict[str, Any] | None = None,
        *,
        page: int = 0,
        limit: int = 32,
    ) -> tuple[list[dict], int | None, bool, str]:
        """Browse image posts by sort, author or rating; this is not text search."""

        return self._get_video_result_page("/images", dict(query_params or {}), page=page, limit=limit)

    def search_videos_page(
        self,
        query_params: dict[str, Any],
        *,
        page: int = 0,
        limit: int = 32,
    ) -> tuple[list[dict], int | None, bool, str]:
        """Search Iwara's text index with its own query and sort parameters."""

        return self.search_page(
            "videos",
            query_params,
            page=page,
            limit=limit,
        )

    def search_page(
        self,
        search_type: str,
        query_params: dict[str, Any],
        *,
        page: int = 0,
        limit: int = 32,
    ) -> tuple[list[dict], int | None, bool, str]:
        """Read one page of Iwara's native ``/search`` for any supported type.

        ``users`` and ``playlists`` reject ``views``/``likes`` with HTTP 400,
        so unsupported orders fall back to relevance before reaching the server.
        """

        search_type = str(search_type or "").strip().lower()
        if search_type not in IWARA_SEARCH_SORTS:
            return [], None, False, tr(
                f"Unsupported search type: {search_type}",
                f"不支持的搜索类型：{search_type}",
                f"未対応の検索タイプ: {search_type}",
            )
        query = str(query_params.get("query") or "").strip()
        if not query:
            return [], None, False, tr(
                "Enter a keyword first", "请先输入关键词", "キーワードを入力してください",
            )
        sort = str(query_params.get("sort") or "").strip().lower()
        if sort not in IWARA_SEARCH_SORTS[search_type]:
            sort = "relevance"
        params = {"type": search_type, "query": query, "sort": sort}
        return self._get_video_result_page("/search", params, page=page, limit=limit)

    def _get_video_result_page(
        self,
        endpoint: str,
        query_params: dict[str, Any],
        *,
        page: int,
        limit: int,
    ) -> tuple[list[dict], int | None, bool, str]:
        """Read one remote page without hiding malformed/error responses."""

        params, page_number, requested_limit = self._page_params(query_params, page, limit)
        try:
            data = self._get_json(f"{BASE_API}{endpoint}", params=params)
            if not isinstance(data, dict):
                return [], None, False, tr(
                    f"Unexpected video search response: {type(data).__name__}",
                    f"视频搜索返回了无法识别的数据：{type(data).__name__}",
                    f"動画検索の応答形式を認識できません: {type(data).__name__}",
                )
            results = self._result_list(data)
            total, has_more = self._paging_of(data, endpoint, results, page_number, requested_limit)
            return results, total, has_more, ""
        except Exception as exc:
            # Search failures (403/429/invalid queries) are not evidence that
            # an individual video is private or inaccessible to the account.
            return [], None, False, str(exc) or tr(
                "Search request failed", "搜索请求失败", "検索リクエストに失敗しました",
            )

    @staticmethod
    def _page_params(query_params: dict[str, Any], page: int, limit: int) -> tuple[dict[str, str], int, int]:
        """Query string for one page; returns ``(params, page number, page size)``."""

        try:
            page_number = max(0, int(page))
        except (TypeError, ValueError):
            page_number = 0
        try:
            requested_limit = max(1, min(100, int(limit)))
        except (TypeError, ValueError):
            requested_limit = 32
        params = {
            str(key): str(value)
            for key, value in (query_params or {}).items()
            if str(value).strip()
        }
        params["page"] = str(page_number)
        params["limit"] = str(requested_limit)
        return params, page_number, requested_limit

    @staticmethod
    def _result_list(data: dict) -> list[dict]:
        """The response's result rows; raises ValueError when they are missing or malformed."""

        results = data.get("results", data.get("data"))
        if not isinstance(results, list) and isinstance(results, dict):
            results = results.get("results", results.get("items"))
        if not isinstance(results, list) or any(not isinstance(item, dict) for item in results):
            detail = _extract_api_message(data) or tr(
                "Missing or invalid results list", "缺少有效的结果列表", "有効な結果リストがありません",
            )
            raise ValueError(tr(
                f"Invalid search response: {detail}",
                f"搜索响应无效：{detail}",
                f"検索応答が無効です: {detail}",
            ))
        return results

    @staticmethod
    def _paging_of(
        data: dict, endpoint: str, results: list[dict], page_number: int, requested_limit: int,
    ) -> tuple[int | None, bool]:
        """``(total, has more pages)`` for a result page, trusting only reliable counts."""

        count_value = data.get("count", data.get("total", data.get("totalCount")))
        try:
            total = int(count_value) if count_value is not None else None
        except (TypeError, ValueError):
            total = None
        if total is not None and total < 0:
            total = None
        try:
            effective_limit = max(1, int(data.get("limit", requested_limit)))
        except (TypeError, ValueError):
            effective_limit = requested_limit

        explicit_more = next(
            (
                data[key]
                for key in ("hasNext", "has_next", "hasMore", "has_more")
                if key in data
            ),
            None,
        )
        # Current Iwara responses commonly expose ``count`` as a paging
        # lower-bound rather than a stable total: a full page 0 reports
        # 33, page 1 reports 65, page 2 reports 97, and so on.  Treat the
        # exact ``offset + page size + 1`` shape as a next-page sentinel;
        # publishing it as a total makes the UI invent a growing page
        # count on every navigation.
        count_is_page_sentinel = bool(
            endpoint in {"/videos", "/images"}
            and total is not None
            and len(results) >= effective_limit
            and total == (page_number + 1) * effective_limit + 1
        )
        if explicit_more is not None:
            if isinstance(explicit_more, str):
                has_more = explicit_more.strip().casefold() in {"1", "true", "yes", "y"}
            else:
                has_more = bool(explicit_more)
        elif count_is_page_sentinel:
            has_more = True
        elif total is not None:
            has_more = (page_number + 1) * effective_limit < total
        else:
            has_more = len(results) >= effective_limit
        if not results:
            has_more = False
        if count_is_page_sentinel or effective_limit != requested_limit:
            # Do not invent a last-page number using a page size the
            # server ignored. Next/previous still use its actual limit.
            total = None
        return total, has_more

    def get_videos_by_query(
        self,
        query_params: dict[str, str],
        *,
        max_pages: int = 100,
        max_results: int = 0,
        stop_after_video_ids: set[str] | None = None,
    ) -> tuple[list[dict], str]:
        """Fetch videos from /videos with arbitrary query parameters.

        Args:
            query_params: query-string key/value params, e.g. {"tags": "2d", "sort": "date"}.
            max_pages: safety page cap.
            max_results: hard cap for returned video stubs. 0 means unlimited.
            stop_after_video_ids: stop after the first page containing one of
                these IDs.  This is used by incremental subscription refresh.

        Returns:
            (videos, error_message). Partial results can be returned with error.
        """
        base_params = {str(k): str(v) for k, v in query_params.items() if str(v).strip()}
        legacy_tag = base_params.pop("tag", "")
        if legacy_tag and not base_params.get("tags"):
            base_params["tags"] = legacy_tag
        start_page_raw = base_params.pop("page", "0")
        try:
            start_page = max(0, int(start_page_raw))
        except Exception:
            start_page = 0

        explicit_limit = 0
        if "limit" in base_params:
            try:
                explicit_limit = max(0, int(base_params["limit"]))
            except Exception:
                explicit_limit = 0

        if max_results > 0 and explicit_limit > 0:
            effective_limit = min(max_results, explicit_limit)
        else:
            effective_limit = max_results or explicit_limit

        videos: list[dict] = []
        known_ids = {
            str(video_id or "").strip().casefold()
            for video_id in (stop_after_video_ids or set())
            if str(video_id or "").strip()
        }
        for page in range(start_page, start_page + max_pages):
            page_params = dict(base_params)
            page_params["page"] = str(page)
            try:
                data = self._get_json(f"{BASE_API}/videos", params=page_params)
            except Exception as exc:
                if videos:
                    return videos, str(exc)
                return [], str(exc)

            results = data.get("results", [])
            if not isinstance(results, list) or not results:
                break
            videos.extend(results)
            if effective_limit > 0 and len(videos) >= effective_limit:
                videos = videos[:effective_limit]
                break

            if known_ids and any(
                str(item.get("id", "") or item.get("video_id", "")).strip().casefold()
                in known_ids
                for item in results
                if isinstance(item, dict)
            ):
                break

            count = data.get("count")
            if isinstance(count, int) and len(videos) >= count:
                break

        return videos, ""
