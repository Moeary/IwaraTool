"""Online search and bridge operations mixed into DownloadManager."""
from __future__ import annotations

import re
from typing import Any

import cloudscraper

from .api import IwaraAPI
from .oreno3d import Oreno3DClient
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

        return self._api_call(
            "get_videos_page",
            query_params or {},
            page=page,
            limit=limit,
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
        match = re.search(r"/video/([^/?#]+)", external_url)
        author = detail.author
        return {
            "video_id": match.group(1) if match else "",
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
        max_videos: int = 8,
        parallel: bool = False,
    ) -> dict[str, Any]:
        """Map an Oreno3D author page to an Iwara author when possible.

        The Oreno author page is the durable anchor.  We first try the same
        author name against Iwara's profile endpoint, then inspect a bounded
        number of Oreno works and use any surviving Iwara link as a fallback.
        This keeps author navigation useful after the originally selected
        Iwara video has been deleted.
        """

        source_id = str(source_id or "").strip()
        oreno3d_url = str(oreno3d_url or "").strip()
        resolved_author_url = str(author_url or "").strip()
        resolved_author_name = str(author_name or "").strip()
        detail_records: list[Any] = []

        if parallel:
            session = cloudscraper.create_scraper(
                browser={"browser": "chrome", "platform": "windows", "mobile": False}
            )
            session.proxies = dict(getattr(self.api.scraper, "proxies", {}) or {})
            try:
                client = Oreno3DClient(session)
                if not resolved_author_url and source_id and oreno3d_url:
                    original = client.fetch_detail_url(source_id, oreno3d_url)
                    if original.author:
                        resolved_author_url = original.author.url
                        resolved_author_name = resolved_author_name or original.author.name
                if resolved_author_url:
                    listings, _last_page = client.fetch_author_page(resolved_author_url, page=1)
                    for listing in listings[: max(1, min(20, int(max_videos)))]:
                        try:
                            detail_records.append(client.fetch_detail(listing))
                        except Exception:
                            continue
            finally:
                session.close()
        else:
            with self._api_lock:
                client = Oreno3DClient(self.api.scraper)
                if not resolved_author_url and source_id and oreno3d_url:
                    original = client.fetch_detail_url(source_id, oreno3d_url)
                    if original.author:
                        resolved_author_url = original.author.url
                        resolved_author_name = resolved_author_name or original.author.name
                if resolved_author_url:
                    listings, _last_page = client.fetch_author_page(resolved_author_url, page=1)
                    for listing in listings[: max(1, min(20, int(max_videos)))]:
                        try:
                            detail_records.append(client.fetch_detail(listing))
                        except Exception:
                            continue

        result: dict[str, Any] = {
            "oreno_author_url": resolved_author_url,
            "oreno_author_name": resolved_author_name,
        }
        if not resolved_author_name:
            for detail in detail_records:
                if detail.author:
                    resolved_author_name = detail.author.name
                    result["oreno_author_name"] = resolved_author_name
                    result["oreno_author_url"] = detail.author.url
                    break

        def _profile_target(profile: object) -> dict[str, str]:
            profile_map = _dict_or_empty(profile)
            user = _dict_or_empty(profile_map.get("user"))
            username = str(user.get("username", "") or user.get("slug", "") or "").strip()
            if not username:
                return {}
            avatar_url = _iwara_image_url(_dict_or_empty(user.get("avatar")), variant="thumbnail")
            return {
                "username": username,
                "name": str(user.get("name", "") or username).strip() or username,
                "id": str(user.get("id", "") or "").strip(),
                "avatar_url": avatar_url,
                "profile_url": f"https://www.iwara.tv/profile/{username}",
            }

        if resolved_author_name:
            try:
                profile, _profile_error = self._api_call("get_user_profile", resolved_author_name)
            except Exception:
                profile = None
            target = _profile_target(profile)
            if target:
                result["iwara_author"] = target
                return result

        for detail in detail_records:
            external_url = str(getattr(detail, "external_video_url", "") or "")
            match = re.search(r"/video/([^/?#]+)", external_url)
            if not match:
                continue
            try:
                metadata, _metadata_error = self.get_iwara_video_info(match.group(1))
            except Exception:
                metadata = None
            target = _profile_target({"user": _dict_or_empty(metadata).get("user")})
            if target:
                result["iwara_author"] = target
                return result
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

