"""Search Oreno3D and resolve its cards and authors to Iwara."""

from __future__ import annotations

from typing import Any

import cloudscraper

from ..oreno3d.client import Oreno3DClient
from ..oreno3d.page_parsing import extract_iwara_video_id
from ..oreno3d.search_query import intersect_oreno3d_listings
from ..task_metadata import _dict_or_empty, _iwara_image_url


class Oreno3DBridgeMixin:
    """Search Oreno3D and resolve its cards and authors to Iwara."""

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
        result: dict[str, Any] = {
            "oreno_author_url": str(author_url or "").strip(),
            "oreno_author_name": str(author_name or "").strip(),
        }

        target = _iwara_profile_target(iwara_author)
        if target:
            result["iwara_author"] = target
            return result

        lookup = _OrenoAuthorLookup(self, result, max_videos)
        # The selected work may still be available even when the author's
        # newest works are deleted. Reuse its resolved ID before crawling.
        known_video_id = str(iwara_video_id or "").strip()
        if result["oreno_author_url"] and lookup.try_video(known_video_id):
            return result

        if parallel:
            session = cloudscraper.create_scraper(
                browser={"browser": "chrome", "platform": "windows", "mobile": False}
            )
            session.proxies = dict(getattr(self.api.scraper, "proxies", {}) or {})
            try:
                lookup.resolve(Oreno3DClient(session), source_id, oreno3d_url, known_video_id)
            finally:
                session.close()
        else:
            with self._api_lock:
                lookup.resolve(Oreno3DClient(self.api.scraper), source_id, oreno3d_url, known_video_id)
        if lookup.errors and "iwara_author" not in result:
            result["error"] = lookup.errors[0]
        return result

    def resolve_oreno3d_video_url(self, source_id: str, oreno3d_url: str) -> str:
        """Resolve an Oreno3D card and build its canonical Iwara URL from the ID."""

        video_id = self.resolve_oreno3d_video_id(source_id, oreno3d_url)
        return f"https://www.iwara.tv/video/{video_id}" if video_id else ""


def _iwara_profile_target(profile: object) -> dict[str, str]:
    """Subscription target fields for an Iwara profile / user payload, or {}."""

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


class _OrenoAuthorLookup:
    """One Oreno3D -> Iwara author lookup: the videos tried, errors and the result."""

    def __init__(self, manager, result: dict[str, Any], max_videos: int):
        self.manager = manager
        self.result = result
        self.max_videos = max_videos
        self.checked_video_ids: set[str] = set()
        self.errors: list[str] = []

    def try_video(self, video_id: str) -> bool:
        """True once ``video_id``'s Iwara uploader has been recorded in the result."""

        if not video_id or video_id in self.checked_video_ids:
            return False
        self.checked_video_ids.add(video_id)
        try:
            metadata, metadata_error = self.manager.get_iwara_video_info(video_id)
            if metadata_error:
                self.errors.append(str(metadata_error))
        except Exception as exc:
            self.errors.append(str(exc))
            metadata = None
        target = _iwara_profile_target({"user": _dict_or_empty(metadata).get("user")})
        if target:
            self.result["iwara_author"] = target
            return True
        return False

    def try_detail(self, detail: Any) -> bool:
        return self.try_video(extract_iwara_video_id(
            str(getattr(detail, "external_video_url", "") or "")
        ))

    def resolve(self, client: Oreno3DClient, source_id: str, oreno3d_url: str, known_video_id: str):
        """Find the author page if needed, then try the known video and the author's works."""

        result = self.result
        original = None
        if not result["oreno_author_url"] and source_id and oreno3d_url:
            try:
                original = client.fetch_detail_url(source_id, oreno3d_url)
                if original.author:
                    result["oreno_author_url"] = original.author.url
                    result["oreno_author_name"] = result["oreno_author_name"] or original.author.name
                    result["oreno_author_id"] = original.author.source_id
            except Exception as exc:
                self.errors.append(str(exc))
        if self.try_video(known_video_id) or (original is not None and self.try_detail(original)):
            return
        author_page = str(result["oreno_author_url"] or "").strip()
        if not author_page:
            return
        try:
            listings, _last_page = client.fetch_author_page(author_page, page=1)
        except Exception as exc:
            self.errors.append(str(exc))
            return
        for listing in listings[: max(1, min(20, int(self.max_videos)))]:
            try:
                detail = client.fetch_detail(listing)
            except Exception as exc:
                self.errors.append(str(exc))
                continue
            if self.try_detail(detail):
                return
