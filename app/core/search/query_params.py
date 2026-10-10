"""Build Iwara search request parameters and recognize profile / playlist references."""

from __future__ import annotations

import re


_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
from .models import IWARA_NATIVE_SEARCH_SORTS, IWARA_NATIVE_SEARCH_TYPES, SearchFilters


def playlist_reference(value: str) -> str:
    """Return a playlist ID for a playlist URL or bare UUID, else ``""``.

    Free text in the playlist scope is a native search query; only explicit
    references open a playlist's videos directly.
    """

    text = str(value or "").strip()
    match = re.search(r"/playlist/([A-Za-z0-9_-]+)", text)
    if match:
        return match.group(1)
    return text if _UUID_RE.match(text) else ""


def profile_reference(value: str) -> str:
    """Return a username for ``@name`` or an iwara.tv profile URL, else ``""``."""

    text = str(value or "").strip()
    match = re.search(r"iwara\.tv/profile/([^/?#\s]+)", text)
    if match:
        return match.group(1)
    if text.startswith("@") and len(text) > 1 and not re.search(r"\s", text):
        return text[1:]
    return ""


def build_video_query_params(filters: SearchFilters, page: int = 0) -> dict[str, str]:
    """Build conservative ``/videos`` parameters understood by current API."""

    params = {
        "page": str(max(0, int(page))),
        "limit": str(max(1, min(100, int(filters.page_size or 32)))),
        "sort": str(filters.sort or "date"),
    }
    if filters.rating:
        params["rating"] = filters.rating
    if filters.author_id.strip():
        params["user"] = filters.author_id.strip()
    # Preserve translated names containing spaces until the tag dictionary
    # resolves them; keyword text belongs to /search, never /videos?q=.
    include_tags = tuple(tag.strip() for tag in filters.include_tags if tag.strip())
    if include_tags:
        params["tags"] = ",".join(include_tags)
    return params


IWARA_KEYWORD_SORTS = ("relevance", "date", "views", "likes")


def build_image_query_params(filters: SearchFilters, page: int = 0) -> dict[str, str]:
    """Build ``/images`` browse parameters (no text query)."""

    params = build_video_query_params(filters, page)
    params.pop("tags", None)  # image browsing has no tag filter in this app
    return params


def build_keyword_query_params(filters: SearchFilters, page: int = 0) -> dict[str, str]:
    """Build /search parameters without converting text into tags."""

    return {
        "type": "videos",
        "query": str(filters.keyword or "").strip(),
        "sort": filters.sort if filters.sort in IWARA_KEYWORD_SORTS else "relevance",
        "page": str(max(0, int(page))),
        "limit": str(max(1, min(100, int(filters.page_size or 32)))),
    }


def build_native_query_params(
    filters: SearchFilters, scope: str, page: int = 0,
) -> dict[str, str]:
    """Build native ``/search`` parameters for images, users or playlists."""

    sorts = IWARA_NATIVE_SEARCH_SORTS.get(scope, ("relevance",))
    return {
        "type": IWARA_NATIVE_SEARCH_TYPES.get(scope, "videos"),
        "query": str(filters.keyword or "").strip(),
        "sort": filters.sort if filters.sort in sorts else "relevance",
        "page": str(max(0, int(page))),
        "limit": str(max(1, min(100, int(filters.page_size or 32)))),
    }
