"""Pure data and filtering helpers for the Fluent search page.

The API has changed its search surface a few times.  Keeping normalization and
local filtering here lets the UI remain stable while the HTTP adapter stays
small and easy to replace later with a local full-text index.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal



SearchScope = Literal["videos", "images", "authors", "tags", "playlists"]

# Iwara's native ``/search`` type for each scope that has a text index.
IWARA_NATIVE_SEARCH_TYPES: dict[str, str] = {
    "videos": "videos",
    "images": "images",
    "authors": "users",
    "playlists": "playlists",
}
# Orders accepted by ``/search``; users and playlists reject views/likes.
IWARA_NATIVE_SEARCH_SORTS: dict[str, tuple[str, ...]] = {
    "videos": ("relevance", "date", "views", "likes"),
    "images": ("relevance", "date", "views", "likes"),
    "authors": ("relevance", "date"),
    "playlists": ("relevance", "date"),
}
IWARA_IMAGE_SOURCE_KIND = "iwara_image"


@dataclass(slots=True)
class SearchFilters:
    """User-selected search options.

    ``date_from`` and ``date_to`` are inclusive. Local filters are available
    for collections, but Iwara keyword searches use the remote search engine
    and keep its matching and ranking semantics.
    """

    keyword: str = ""
    title_mode: Literal["all", "any"] = "all"
    sort: str = "date"
    rating: str = ""
    include_tags: tuple[str, ...] = ()
    exclude_tags: tuple[str, ...] = ()
    author: str = ""
    author_id: str = ""
    author_any: tuple[str, ...] = ()
    author_not: tuple[str, ...] = ()
    origin_any: tuple[str, ...] = ()
    origin_not: tuple[str, ...] = ()
    character_any: tuple[str, ...] = ()
    character_not: tuple[str, ...] = ()
    tag_all: tuple[str, ...] = ()
    tag_any: tuple[str, ...] = ()
    tag_not: tuple[str, ...] = ()
    min_views: int | None = None
    max_views: int | None = None
    min_likes: int | None = None
    max_likes: int | None = None
    min_duration: float | None = None
    max_duration: float | None = None
    date_from: date | None = None
    date_to: date | None = None
    page_size: int = 32


@dataclass(slots=True)
class SearchVideo:
    """Stable video shape consumed by the UI."""

    video_id: str
    title: str
    author_username: str = ""
    author_name: str = ""
    published_at: str = ""
    likes: int = 0
    views: int = 0
    duration: float = 0.0
    comments: int = 0
    rating: str = ""
    tags: tuple[str, ...] = ()
    origins: tuple[str, ...] = ()
    characters: tuple[str, ...] = ()
    thumbnail_url: str = ""
    source_url: str = ""
    slug: str = ""
    source_kind: str = "iwara"
    download_video_id: str = ""
    downloadable: bool = True
    iwara_url: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(slots=True)
class SearchAuthor:
    """Stable author shape consumed by the UI."""

    author_id: str
    username: str
    name: str = ""
    avatar_url: str = ""
    bio: str = ""
    video_count: int = 0
    source_url: str = ""
    joined_at: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(slots=True)
class SearchPlaylist:
    """Stable playlist shape returned by Iwara's native playlist search."""

    playlist_id: str
    title: str
    video_count: int = 0
    author_username: str = ""
    author_name: str = ""
    thumbnail_url: str = ""
    source_url: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(slots=True)
class SearchPageResult:
    """One result page, including state for explicit page navigation."""

    scope: SearchScope
    videos: list[SearchVideo] = field(default_factory=list)
    authors: list[SearchAuthor] = field(default_factory=list)
    playlists: list[SearchPlaylist] = field(default_factory=list)
    total: int | None = None
    has_more: bool = False
    next_page: int | None = None
    scanned_pages: int = 0
    error: str = ""
    current_page: int = 0
    last_page: int | None = None
