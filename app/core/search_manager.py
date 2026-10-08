"""Online search and bridge operations mixed into DownloadManager."""
from __future__ import annotations

from typing import Any

import cloudscraper

from ..i18n import tr
from .api import IwaraAPI
from .oreno3d import Oreno3DClient, extract_iwara_video_id
from .oreno3d_search import intersect_oreno3d_listings
from .task_metadata import _dict_or_empty, _iwara_image_url


class SearchManagerMixin:
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

    def get_search_playlist_videos(self, playlist_id: str, *, max_pages: int = 4) -> list[dict]:
        """Fetch a bounded playlist result set for the search interface."""

        return self._api_call(
            "get_playlist_videos",
            str(playlist_id or "").strip(),
            max_pages=max(0, min(20, int(max_pages))),
        )

    def get_oreno3d_search_page(
        self,
        keyword: str,
        *,
        page: int = 1,
        sort: str = "latest",
        search_type: str | None = None,
        entity_id: str | None = None,
    ):
        """Forward one free-text or entity page to Oreno3D."""

        with self._api_lock:
            client = Oreno3DClient(self.api.scraper)
            if str(keyword or "").strip() or (search_type and entity_id):
                return client.fetch_search_page(
                    keyword,
                    page=page,
                    sort=sort,
                    search_type=search_type,
                    entity_id=entity_id,
                )
            return client.fetch_listing_page(page=page, sort=sort)

    def get_oreno3d_tag_search_page(
        self,
        tags: list[str] | tuple[str, ...],
        *,
        page: int = 1,
        sort: str = "latest",
    ):
        """Search several Oreno3D tags and return their intersection.

        Numeric Oreno3D IDs use their entity routes; mapped Iwara labels are
        resolved to the corresponding typed route and unknown names use the
        public keyword endpoint.  Multi-tag matching is a client-side
        intersection of the same result page from each query.  The smallest
        result page count is used for pagination.
        """

        unique_tags: list[str] = []
        seen: set[str] = set()
        for value in tags:
            tag = str(value or "").strip()
            if not tag or tag.casefold() in seen:
                continue
            seen.add(tag.casefold())
            unique_tags.append(tag)
        if not unique_tags:
            return [], 0
        if len(unique_tags) == 1:
            return self.get_oreno3d_search_page(
                "",
                page=page,
                sort=sort,
                search_type="tag",
                entity_id=unique_tags[0],
            )

        with self._api_lock:
            client = Oreno3DClient(self.api.scraper)
            pages = [
                client.fetch_entity_page("tag", tag, page=page, sort=sort)
                for tag in unique_tags
            ]
        groups = [items for items, _last_page in pages]
        last_page = min((last_page for _items, last_page in pages), default=0)
        return intersect_oreno3d_listings(groups), last_page

    def resolve_oreno3d_video_details(
        self,
        source_id: str,
        oreno3d_url: str,
        *,
        parallel: bool = False,
    ) -> dict[str, Any]:
        """Resolve one Oreno3D card and keep its durable author metadata.

        Normal calls reuse the shared scraper and remain serialized with the
        rest of the API traffic.  Search-page workers can opt into ``parallel``
        to use a short-lived independent cloudscraper session per task.  This
        keeps configurable Oreno3D ID resolution genuinely concurrent without
        making the stateful shared API session thread-unsafe.
        """

        source_id = str(source_id or "").strip()
        oreno3d_url = str(oreno3d_url or "").strip()
        if not source_id or not oreno3d_url:
            return {}
        if parallel:
            session = cloudscraper.create_scraper(
                browser={"browser": "chrome", "platform": "windows", "mobile": False}
            )
            # ``apply_config`` keeps the shared session's proxy current.  Copy
            # only its proxy mapping; cookies and auth state are not needed for
            # the public Oreno3D detail page.
            session.proxies = dict(getattr(self.api.scraper, "proxies", {}) or {})
            try:
                detail = Oreno3DClient(session).fetch_detail_url(
                    source_id,
                    oreno3d_url,
                )
            finally:
                session.close()
        else:
            with self._api_lock:
                detail = Oreno3DClient(self.api.scraper).fetch_detail_url(
                    source_id,
                    oreno3d_url,
                )
        external_url = detail.external_video_url
        author = detail.author
        return {
            "video_id": extract_iwara_video_id(external_url),
            "video_url": external_url,
            "oreno_author_id": author.source_id if author else "",
            "oreno_author_name": author.name if author else "",
            "oreno_author_url": author.url if author else "",
            "oreno_title": detail.title,
        }

    def resolve_oreno3d_video_id(
        self,
        source_id: str,
        oreno3d_url: str,
        *,
        parallel: bool = False,
    ) -> str:
        """Resolve one Oreno3D card to its linked Iwara video ID."""

        return str(
            self.resolve_oreno3d_video_details(
                source_id,
                oreno3d_url,
                parallel=parallel,
            ).get("video_id", "")
            or ""
        )

    def resolve_oreno3d_author(
        self,
        source_id: str = "",
        oreno3d_url: str = "",
        *,
        author_url: str = "",
        author_name: str = "",
        iwara_author: dict[str, Any] | None = None,
        iwara_video_id: str = "",
        max_videos: int = 8,
        parallel: bool = False,
    ) -> dict[str, Any]:
        """Map an Oreno3D author page to an Iwara author when possible.

        Reuse a saved Iwara mapping or author page first, then inspect a bounded
        number of works for a surviving Iwara link. An Oreno display name is
        never sufficient evidence of an Iwara username.
        """

        source_id = str(source_id or "").strip()
        oreno3d_url = str(oreno3d_url or "").strip()
        resolved_author_url = str(author_url or "").strip()
        resolved_author_name = str(author_name or "").strip()
        result: dict[str, Any] = {
            "oreno_author_url": resolved_author_url,
            "oreno_author_name": resolved_author_name,
        }

        def _profile_target(profile: object) -> dict[str, str]:
            profile_map = _dict_or_empty(profile)
            user = _dict_or_empty(profile_map.get("user")) or profile_map
            username = str(user.get("username", "") or user.get("slug", "") or "").strip()
            if not username:
                return {}
            avatar_url = str(user.get("avatar_url") or "").strip() or _iwara_image_url(
                _dict_or_empty(user.get("avatar")), variant="thumbnail"
            )
            return {
                "username": username,
                "name": str(user.get("name", "") or username).strip() or username,
                "id": str(user.get("id", "") or "").strip(),
                "avatar_url": avatar_url,
                "profile_url": f"https://www.iwara.tv/profile/{username}",
            }

        target = _profile_target(iwara_author)
        if target:
            result["iwara_author"] = target
            return result

        checked_video_ids: set[str] = set()
        errors: list[str] = []

        def _try_video(video_id: str) -> bool:
            if not video_id or video_id in checked_video_ids:
                return False
            checked_video_ids.add(video_id)
            try:
                metadata, metadata_error = self.get_iwara_video_info(video_id)
                if metadata_error:
                    errors.append(str(metadata_error))
            except Exception as exc:
                errors.append(str(exc))
                metadata = None
            target = _profile_target({"user": _dict_or_empty(metadata).get("user")})
            if target:
                result["iwara_author"] = target
                return True
            return False

        def _try_detail(detail: Any) -> bool:
            return _try_video(extract_iwara_video_id(
                str(getattr(detail, "external_video_url", "") or "")
            ))

        # The selected work may still be available even when the author's
        # newest works are deleted. Reuse its resolved ID before crawling.
        known_video_id = str(iwara_video_id or "").strip()
        if result["oreno_author_url"] and _try_video(known_video_id):
            return result

        def _resolve(client: Oreno3DClient):
            original = None
            if not result["oreno_author_url"] and source_id and oreno3d_url:
                try:
                    original = client.fetch_detail_url(source_id, oreno3d_url)
                    if original.author:
                        result["oreno_author_url"] = original.author.url
                        result["oreno_author_name"] = result["oreno_author_name"] or original.author.name
                        result["oreno_author_id"] = original.author.source_id
                except Exception as exc:
                    errors.append(str(exc))
            if _try_video(known_video_id) or (original is not None and _try_detail(original)):
                return
            author_page = str(result["oreno_author_url"] or "").strip()
            if not author_page:
                return
            try:
                listings, _last_page = client.fetch_author_page(author_page, page=1)
            except Exception as exc:
                errors.append(str(exc))
                return
            for listing in listings[: max(1, min(20, int(max_videos)))]:
                try:
                    detail = client.fetch_detail(listing)
                except Exception as exc:
                    errors.append(str(exc))
                    continue
                if _try_detail(detail):
                    return

        if parallel:
            session = cloudscraper.create_scraper(
                browser={"browser": "chrome", "platform": "windows", "mobile": False}
            )
            session.proxies = dict(getattr(self.api.scraper, "proxies", {}) or {})
            try:
                _resolve(Oreno3DClient(session))
            finally:
                session.close()
        else:
            with self._api_lock:
                _resolve(Oreno3DClient(self.api.scraper))
        if errors and "iwara_author" not in result:
            result["error"] = errors[0]
        return result

    def resolve_oreno3d_video_url(self, source_id: str, oreno3d_url: str) -> str:
        """Resolve an Oreno3D card and build its canonical Iwara URL from the ID."""

        video_id = self.resolve_oreno3d_video_id(source_id, oreno3d_url)
        return f"https://www.iwara.tv/video/{video_id}" if video_id else ""

    def get_iwara_video_info(self, video_id: str) -> tuple[dict | None, str]:
        """Fetch one Iwara video's metadata for online search result hydration."""

        video_id = str(video_id or "").strip()
        if not video_id:
            return None, ""
        return self._api_call("get_video_info", video_id)

    def get_search_tag_suggestions(self, query: str, *, limit: int = 16):
        """Return offline multilingual tag candidates for the search UI."""

        return self.tag_dictionary.suggest(query, limit=limit)

    def canonical_search_tag(self, value: str) -> str:
        """Normalize a localized tag label to its canonical search key."""

        return self.tag_dictionary.canonical_key(value)

    def update_search_tag_dictionary(self) -> tuple[int, str]:
        """Refresh the cached LoveIwara multilingual tag dictionary."""

        with self._api_lock:
            return self.tag_dictionary.update_from_remote(self.api.scraper)

    def create_worker_api_client(self) -> IwaraAPI:
        """Create an isolated API session for background network work.

        The main API session is intentionally serialized because it is shared
        by login, parsing and download metadata.  Image and subscription
        workers can safely use independent cloudscraper sessions while
        carrying over the current token and proxy configuration.
        """
        with self._api_lock:
            token = self.api.token or ""
            proxies = dict(getattr(self.api.scraper, "proxies", {}) or {})
        client = IwaraAPI()
        client.token = token or None
        client.scraper.proxies = proxies
        return client

    @staticmethod
    def close_worker_api_client(client: IwaraAPI | None):
        if client is None:
            return
        close = getattr(getattr(client, "scraper", None), "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass

    def cache_search_image(
        self,
        kind: str,
        item_key: str,
        image_url: str,
        *,
        api_client: IwaraAPI | None = None,
    ) -> str:
        """Cache one search card image using an isolated or shared API session."""

        if api_client is None:
            with self._api_lock:
                client = self.api
                token = client.token or ""
        else:
            client = api_client
            token = client.token or ""
        headers = {
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            "Referer": (
                "https://oreno3d.com/"
                if "oreno3d.com" in str(image_url or "").casefold()
                else "https://www.iwara.tv/"
            ),
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return self.search_image_cache.get_or_fetch(
            kind,
            item_key,
            image_url,
            session=client.scraper,
            headers=headers,
        )

