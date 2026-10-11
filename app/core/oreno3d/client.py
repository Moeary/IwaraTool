"""Small Oreno3D HTML client for online search and bounded detail lookups.

The reference project uses ``selectolax``.  IwaraTool deliberately keeps its
current dependency set small, so this module contains a conservative DOM
parser for the stable Oreno3D selectors used by its listing/detail pages.
"""

from __future__ import annotations

from urllib.parse import quote, urlparse

from requests.exceptions import ConnectionError, Timeout

from ..net_policy import cancellable_sleep
from .iwara_mapping import resolve_oreno3d_entity
from .dom import _clean_text
from .models import Oreno3DDetail, Oreno3DListing
from .page_parsing import BASE_URL, _ENTITY_PATHS, parse_detail_page, parse_listing_page


class Oreno3DClient:
    """Synchronous client for user-triggered Oreno3D requests."""

    def __init__(self, session, *, timeout: int = 30, delay: float = 0.15):
        self.session = session
        self.timeout = max(5, int(timeout))
        self.delay = max(0.0, float(delay))

    def _get(self, url: str, **kwargs):
        """Retry one transient public-page failure, retaining terminal errors."""

        for attempt in range(2):
            try:
                response = self.session.get(
                    url,
                    headers={"Accept": "text/html,application/xhtml+xml", "Referer": BASE_URL + "/"},
                    timeout=self.timeout,
                    **kwargs,
                )
            except (ConnectionError, Timeout):
                if attempt:
                    raise
            else:
                status = int(getattr(response, "status_code", 0) or 0)
                if not attempt and status in {429, 502, 503, 504}:
                    retry_after = str(getattr(response, "headers", {}).get("Retry-After", ""))
                    # Do not shorten a server-requested cooldown or block a
                    # worker for an unbounded amount of time.
                    if retry_after and (not retry_after.isdigit() or int(retry_after) > 2):
                        return response
                    response.close()
                    cancellable_sleep(max(self.delay, float(retry_after or 0), 0.25))
                    continue
                return response
            cancellable_sleep(max(self.delay, 0.25))

    def fetch_listing_page(self, page: int, *, sort: str = "latest") -> tuple[list[Oreno3DListing], int]:
        params = {"sort": sort, "page": max(1, int(page))}
        return self._fetch_listing_path("/", params=params, page=page)

    def _fetch_listing_path(
        self,
        path: str,
        *,
        params: dict[str, object],
        page: int,
    ) -> tuple[list[Oreno3DListing], int]:
        response = self._get(
            f"{BASE_URL}{path}",
            params=params,
        )
        try:
            if int(getattr(response, "status_code", 0) or 0) >= 400:
                raise RuntimeError(f"Oreno3D HTTP {response.status_code}")
            return parse_listing_page(response.text, page=max(1, int(page)))
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()

    def fetch_search_page(
        self,
        keyword: str,
        page: int = 1,
        *,
        sort: str = "latest",
        search_type: str | None = None,
        entity_id: str | None = None,
    ) -> tuple[list[Oreno3DListing], int]:
        """Query Oreno3D's free-text or entity search endpoint.

        Oreno3D exposes the same card markup on ``/search`` and on numeric
        entity routes such as ``/tags/{id}``.  Human-readable labels are first
        resolved through the bundled strict-unique Iwara/Oreno map; unknown
        labels fall back to the site's regular keyword search instead of
        producing a 404.
        """

        normalized_type = _clean_text(search_type).casefold()
        normalized_id = _clean_text(entity_id).strip("/")
        if ":" in normalized_id:
            prefix, value = normalized_id.split(":", 1)
            if prefix.casefold() in _ENTITY_PATHS and value.strip():
                normalized_type = prefix.casefold()
                normalized_id = value.strip()
        if normalized_type in _ENTITY_PATHS and normalized_id and not normalized_id.isdigit():
            resolved = resolve_oreno3d_entity(normalized_id)
            if resolved is not None:
                normalized_type = resolved.kind
                normalized_id = resolved.entity_id
        path = "/search"
        params: dict[str, object] = {"page": max(1, int(page))}
        if (
            normalized_type in _ENTITY_PATHS
            and normalized_id
            and normalized_id.isdigit()
        ):
            path = f"/{_ENTITY_PATHS[normalized_type]}/{quote(normalized_id, safe='')}"
        else:
            # ``tag:azur_lane`` and similar inputs are names, not Oreno's
            # numeric entity IDs.  Its public search endpoint accepts these
            # names and is preferable to requesting /tags/azur_lane (404).
            params["keyword"] = _clean_text(keyword) or normalized_id
        if sort:
            params["sort"] = sort
        return self._fetch_listing_path(path, params=params, page=page)

    def fetch_entity_page(
        self,
        search_type: str,
        entity_id: str,
        page: int = 1,
        *,
        sort: str = "latest",
    ) -> tuple[list[Oreno3DListing], int]:
        """Fetch a tag, origin, or character result page."""

        return self.fetch_search_page(
            "",
            page=page,
            sort=sort,
            search_type=search_type,
            entity_id=entity_id,
        )

    def fetch_author_page(
        self,
        author_id_or_url: str,
        page: int = 1,
        *,
        sort: str = "latest",
    ) -> tuple[list[Oreno3DListing], int]:
        """Fetch one Oreno3D author's video listing.

        Author pages are stable even when an individual linked Iwara video is
        later removed, so callers can use this as the durable source for an
        author mapping and subscription entry.
        """

        value = _clean_text(author_id_or_url).strip()
        parsed = urlparse(value if "://" in value else "")
        path = parsed.path if parsed.path.startswith("/authors/") else ""
        if not path:
            author_id = value.strip("/").rsplit("/", 1)[-1]
            path = f"/authors/{quote(author_id, safe='')}"
        params: dict[str, object] = {"page": max(1, int(page))}
        if sort:
            params["sort"] = sort
        return self._fetch_listing_path(path, params=params, page=page)

    def fetch_detail_url(self, source_id: str, oreno3d_url: str) -> Oreno3DDetail:
        """Fetch one detail page without creating a local Oreno3D mirror."""

        response = self._get(oreno3d_url)
        try:
            if int(getattr(response, "status_code", 0) or 0) >= 400:
                raise RuntimeError(f"Oreno3D HTTP {response.status_code}")
            return parse_detail_page(
                response.text,
                source_id=str(source_id or "").strip(),
                oreno3d_url=oreno3d_url,
            )
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()

    def fetch_detail(self, item: Oreno3DListing) -> Oreno3DDetail:
        return self.fetch_detail_url(item.source_id, item.oreno3d_url)
