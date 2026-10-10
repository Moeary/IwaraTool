"""Iwara search pages, item details, comments, likes and follows for the UI."""

from __future__ import annotations

from ...i18n import tr
from ..api.client import IwaraAPI


class IwaraSearchMixin:
    """Iwara search pages, item details, comments, likes and follows for the UI."""

    def get_search_video_page(
        self,
        query_params: dict[str, str] | None = None,
        *,
        page: int = 0,
        limit: int = 32,
    ) -> tuple[list[dict], int | None, bool, str]:
        """Fetch one page for the search interface through the shared API session."""

        params = dict(query_params or {})
        if params.get("tags"):
            tags = self.tag_dictionary.resolve_query(params["tags"])
            if not tags:
                raise ValueError(tr(
                    "Enter at least one tag, not only separators.",
                    "请至少输入一个标签，不能只有分隔符。",
                    "区切り文字だけでなく、タグを1つ以上入力してください。",
                ))
            params["tags"] = ",".join(tags)
        return self._api_call(
            "get_videos_page",
            params,
            page=page,
            limit=limit,
        )

    def get_home_page(
        self,
        kind: str,
        query_params: dict[str, str] | None = None,
        *,
        page: int = 0,
        limit: int = 12,
        api_client: IwaraAPI | None = None,
    ) -> tuple[list[dict], int | None, bool, str]:
        """Browse ``/videos`` or ``/images`` for the Home page.

        Home sections run concurrently, so each worker passes its own API
        client; without one the call is serialized on the shared session.
        """

        method = "get_images_page" if str(kind).strip().lower().startswith("image") else "get_videos_page"
        if api_client is not None:
            return getattr(api_client, method)(dict(query_params or {}), page=page, limit=limit)
        return self._api_call(method, dict(query_params or {}), page=page, limit=limit)

    def is_logged_in(self) -> bool:
        """Whether the shared API session carries an account token."""

        return bool(self._current_token())

    def get_image_info(self, image_id: str, *, api_client: IwaraAPI | None = None) -> tuple[dict | None, str]:
        """Fetch one image post (with its files) for the detail view."""

        if api_client is not None:
            return api_client.get_image_info(image_id)
        return self._api_call("get_image_info", str(image_id or "").strip())

    def get_related_items(
        self, kind: str, item_id: str, *, limit: int = 12, api_client: IwaraAPI | None = None,
    ) -> tuple[list[dict], str]:
        """Related videos/images shown under a detail view."""

        if api_client is not None:
            return api_client.get_related(kind, item_id, limit=limit)
        return self._api_call("get_related", kind, item_id, limit=limit)

    def get_item_comments(
        self,
        kind: str,
        item_id: str,
        *,
        page: int = 0,
        parent: str = "",
        api_client: IwaraAPI | None = None,
    ) -> tuple[list[dict], int | None, str]:
        """One page of comments (or replies when ``parent`` is set)."""

        if api_client is not None:
            return api_client.get_comments(kind, item_id, page=page, parent=parent)
        return self._api_call("get_comments", kind, item_id, page=page, parent=parent)

    def get_search_keyword_page(
        self,
        query_params: dict[str, str],
        *,
        page: int = 0,
        limit: int = 32,
    ) -> tuple[list[dict], int | None, bool, str]:
        """Search the remote text index, separately from browsing by tags."""

        return self._api_call("search_videos_page", query_params, page=page, limit=limit)

    def get_search_native_page(
        self,
        search_type: str,
        query_params: dict[str, str],
        *,
        page: int = 0,
        limit: int = 32,
    ) -> tuple[list[dict], int | None, bool, str]:
        """Search Iwara's native index for images, users or playlists."""

        return self._api_call(
            "search_page", search_type, query_params, page=page, limit=limit,
        )

    def get_search_user_profile(self, username: str) -> tuple[dict | None, str]:
        """Fetch one author profile for the search interface."""

        return self._api_call("get_user_profile", str(username or "").strip())

    def set_item_liked(
        self, kind: str, item_id: str, liked: bool, *, api_client: IwaraAPI | None = None,
    ) -> tuple[bool, str]:
        """Like or un-like a post with the signed-in account."""

        if api_client is not None:
            return api_client.set_liked(kind, item_id, liked)
        return self._api_call("set_liked", kind, item_id, liked)

    def set_user_following(
        self, user_id: str, following: bool, *, api_client: IwaraAPI | None = None,
    ) -> tuple[bool, str]:
        """Follow or unfollow an author on the Iwara website."""

        if api_client is not None:
            return api_client.set_following(user_id, following)
        return self._api_call("set_following", user_id, following)

    def get_author_profile(
        self, username: str, *, api_client: IwaraAPI | None = None,
    ) -> tuple[dict | None, str]:
        """An author's profile; carries ``following``/``friend`` when signed in."""

        username = str(username or "").strip().lstrip("@")
        if api_client is not None:
            return api_client.get_user_profile(username)
        return self._api_call("get_user_profile", username)

    def find_author_subscription(self, username: str) -> dict | None:
        """The local author subscription for ``username`` (any case), if any."""

        wanted = str(username or "").strip().lstrip("@").casefold()
        if not wanted:
            return None
        for source in self.subscriptions.list_sources():
            if (
                str(source.get("source_type", "") or "") == "author"
                and str(source.get("source_key", "") or "").strip().casefold() == wanted
            ):
                return source
        return None

    def get_search_playlist_videos(self, playlist_id: str, *, max_pages: int = 4) -> list[dict]:
        """Fetch a bounded playlist result set for the search interface."""

        return self._api_call(
            "get_playlist_videos",
            str(playlist_id or "").strip(),
            max_pages=max(0, min(20, int(max_pages))),
        )

    def get_iwara_video_info(self, video_id: str) -> tuple[dict | None, str]:
        """Fetch one Iwara video's metadata for online search result hydration."""

        video_id = str(video_id or "").strip()
        if not video_id:
            return None, ""
        return self._api_call("get_video_info", video_id)
