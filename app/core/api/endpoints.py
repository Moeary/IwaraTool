"""Iwara API hosts, default headers and the sort orders the search endpoints accept."""

from __future__ import annotations


BASE_API = "https://api.iwara.tv"


ALT_BASE_API = "https://apiq.iwara.tv"


# Origin / Referer / X-Site follow the site being worked for, see core/site.
DEFAULT_API_HEADERS = {
    "Accept": "application/json, text/plain, */*",
}


# Orders accepted by ``/search`` per type (verified against the live API:
# users/playlists answer ``errors.badRequest`` for views and likes).
IWARA_SEARCH_SORTS: dict[str, tuple[str, ...]] = {
    "videos": ("relevance", "date", "views", "likes"),
    "images": ("relevance", "date", "views", "likes"),
    "users": ("relevance", "date"),
    "playlists": ("relevance", "date"),
}
