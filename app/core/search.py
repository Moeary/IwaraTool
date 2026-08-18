"""Pure data and filtering helpers for the Fluent search page.

The API has changed its search surface a few times.  Keeping normalization and
local filtering here lets the UI remain stable while the HTTP adapter stays
small and easy to replace later with a local full-text index.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal, Mapping
from urllib.parse import quote, urlparse


SearchScope = Literal["videos", "authors", "tags", "playlists"]


@dataclass(slots=True)
class SearchFilters:
    """User-selected search options.

    ``date_from`` and ``date_to`` are inclusive.  Keyword matching is local as
    well as server-side: every keyword term must occur in the video's combined
    title/author/tag text.  This makes the result useful even when the server
    ignores an unknown ``q`` query parameter.
    """

    keyword: str = ""
    title_mode: Literal["all", "any"] = "all"
    sort: str = "date"
    rating: str = ""
    include_tags: tuple[str, ...] = ()
    exclude_tags: tuple[str, ...] = ()
    author: str = ""
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
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(slots=True)
class SearchPageResult:
    """One result page, including state for explicit page navigation."""

    scope: SearchScope
    videos: list[SearchVideo] = field(default_factory=list)
    authors: list[SearchAuthor] = field(default_factory=list)
    total: int | None = None
    has_more: bool = False
    next_page: int | None = None
    scanned_pages: int = 0
    error: str = ""
    current_page: int = 0
    last_page: int | None = None


def split_search_terms(value: str) -> tuple[str, ...]:
    """Split comma/space separated tags or search terms consistently."""

    parts = re.split(r"[,，;；|\s]+", str(value or ""))
    terms: list[str] = []
    seen: set[str] = set()
    for part in parts:
        term = part.strip().lstrip("#")
        if not term:
            continue
        key = term.casefold()
        if key not in seen:
            seen.add(key)
            terms.append(term)
    return tuple(terms)


def parse_date(value: Any) -> date | None:
    """Parse the common Iwara ISO timestamp/date forms."""

    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        pass
    try:
        normalized = text.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized).date()
    except ValueError:
        return None


def _as_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(value)
    try:
        if isinstance(value, str):
            value = value.replace(",", "").strip()
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if isinstance(value, str):
            value = value.replace(",", "").strip()
        return float(value)
    except (TypeError, ValueError):
        return default


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float)):
        return str(value).strip()
    return ""


def _first_text(mapping: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = _text(mapping.get(key))
        if value:
            return value
    return ""


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _iwara_image_url(value: Any, *, variant: str = "original") -> str:
    """Build an Iwara image URL from either a URL or image metadata object."""

    if isinstance(value, str):
        return value.strip() if value.strip().startswith(("http://", "https://")) else ""
    if not isinstance(value, Mapping):
        return ""
    image = _mapping(value)
    for key in ("url", "fileUrl", "src", "source", "original", "thumbnail"):
        direct = _iwara_image_url(image.get(key), variant=variant)
        if direct:
            return direct
    image_id = _first_text(image, "id", "fileId", "imageId")
    image_name = _first_text(image, "name", "filename", "fileName")
    if image_id:
        suffix = f"/{quote(image_name)}" if image_name else ""
        return f"https://i.iwara.tv/image/{variant}/{quote(image_id)}{suffix}"
    return ""


def _tags(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return split_search_terms(value)
    if isinstance(value, Mapping):
        value = value.get("results") or value.get("items") or value.get("tags") or []
    if not isinstance(value, (list, tuple, set)):
        return ()
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        if isinstance(item, Mapping):
            # Iwara's /videos endpoint returns canonical tags as
            # ``{"id": "hmv", "type": "category"}`` without a name.
            # Keep the ID so server-side tag results survive local filtering.
            name = _first_text(item, "name", "title", "tag", "id", "slug")
        else:
            name = _text(item)
        if name and name.casefold() not in seen:
            seen.add(name.casefold())
            result.append(name)
    return tuple(result)


def _video_thumbnail(video: Mapping[str, Any]) -> str:
    for key in ("customThumbnail", "thumbnailUrl", "thumbnail_url", "thumbnailImage"):
        url = _iwara_image_url(video.get(key))
        if url:
            return url

    thumbnail = video.get("thumbnail")
    direct = _iwara_image_url(thumbnail)
    if direct:
        return direct

    file_info = _mapping(video.get("file"))
    file_id = _first_text(file_info, "id", "fileId")
    if not file_id:
        file_id = _first_text(video, "fileId", "file_id")
    if not file_id:
        return ""
    host = urlparse(_first_text(video, "fileUrl", "file_url")).netloc
    host = host or "i.iwara.tv"
    index = max(0, _as_int(thumbnail, 0))
    return f"https://{host}/image/original/{quote(file_id)}/thumbnail-{index:02d}.jpg"


def normalize_video(value: Mapping[str, Any] | Any) -> SearchVideo | None:
    """Normalize a video stub or detail response into :class:`SearchVideo`."""

    video = dict(value) if isinstance(value, Mapping) else {}
    video_id = _first_text(video, "id", "videoId", "video_id")
    if not video_id:
        return None
    user = _mapping(video.get("user"))
    profile = _mapping(user.get("profile"))
    author_username = _first_text(user, "username", "slug", "name")
    author_name = _first_text(user, "name", "displayName", "username")
    if profile:
        author_name = _first_text(profile, "name", "displayName") or author_name
    published_at = _first_text(video, "createdAt", "publishedAt", "updatedAt", "date")
    file_info = _mapping(video.get("file"))
    duration = _as_float(
        video.get("duration", file_info.get("duration", video.get("length", 0)))
    )
    slug = _first_text(video, "slug")
    source_url = _first_text(video, "source_url", "url")
    if not source_url:
        source_url = f"https://www.iwara.tv/video/{quote(video_id)}"
    rating = video.get("rating")
    if isinstance(rating, Mapping):
        rating = _first_text(rating, "name", "label", "id")
    rating_text = _text(rating)
    file_url = _first_text(video, "fileUrl", "file_url", "downloadUrl")
    downloadable = bool(file_url or file_info or video.get("file"))
    if "downloadable" in video:
        downloadable = bool(video.get("downloadable"))
    return SearchVideo(
        video_id=video_id,
        title=_first_text(video, "title", "name") or video_id,
        author_username=author_username,
        author_name=author_name,
        published_at=published_at,
        likes=_as_int(video.get("numLikes", video.get("likes", video.get("likeCount", 0)))),
        views=_as_int(video.get("numViews", video.get("views", video.get("viewCount", 0)))),
        duration=duration,
        comments=_as_int(video.get("numComments", video.get("comments", video.get("commentCount", 0)))),
        rating=rating_text,
        tags=_tags(video.get("tags")),
        origins=_tags(video.get("origins")),
        characters=_tags(video.get("characters")),
        thumbnail_url=_video_thumbnail(video),
        source_url=source_url,
        slug=slug,
        source_kind=_first_text(video, "source_kind", "source") or "iwara",
        download_video_id=_first_text(video, "download_video_id", "iwara_id"),
        downloadable=downloadable,
        iwara_url=source_url if "/video/" in source_url else "",
        raw=video,
    )


def normalize_oreno3d_listing(value: Mapping[str, Any] | Any) -> SearchVideo | None:
    """Normalize one card returned by Oreno3D's online ``/search`` page."""

    if isinstance(value, Mapping):
        source_id = _first_text(value, "source_id", "id", "video_id")
        title = _first_text(value, "title", "name")
        author = _first_text(value, "author_name", "author", "username")
        source_url = _first_text(value, "oreno3d_url", "source_url", "url")
        thumbnail = _first_text(value, "thumbnail_url", "thumbnail")
        iwara_url = _first_text(value, "iwara_url", "external_video_url")
        views = _as_int(value.get("view_count", value.get("views", 0)))
        likes = _as_int(value.get("favorite_count", value.get("likes", 0)))
        tags = _tags(value.get("tags"))
        raw = dict(value)
    else:
        source_id = _text(getattr(value, "source_id", ""))
        title = _text(getattr(value, "title", ""))
        author = _text(getattr(value, "author_name", ""))
        source_url = _text(getattr(value, "oreno3d_url", ""))
        thumbnail = _text(getattr(value, "thumbnail_url", ""))
        iwara_url = _text(getattr(value, "iwara_url", ""))
        views = _as_int(getattr(value, "view_count", 0))
        likes = _as_int(getattr(value, "favorite_count", 0))
        tags = _tags(getattr(value, "tags", ()))
        raw = {
            "source_id": source_id,
            "title": title,
            "author_name": author,
            "oreno3d_url": source_url,
            "thumbnail_url": thumbnail,
            "view_count": views,
            "favorite_count": likes,
            "tags": list(tags),
        }
    if not source_id:
        return None
    return SearchVideo(
        video_id=f"oreno3d:{source_id}",
        title=title or source_id,
        author_username=author,
        author_name=author,
        likes=likes,
        views=views,
        tags=tags,
        thumbnail_url=thumbnail,
        source_url=source_url or f"https://oreno3d.com/movies/{quote(source_id)}",
        source_kind="oreno3d",
        download_video_id="",
        downloadable=False,
        iwara_url=iwara_url,
        raw=raw,
    )


def normalize_author(value: Mapping[str, Any] | Any) -> SearchAuthor | None:
    """Normalize a profile response or compact ``user`` object."""

    raw = dict(value) if isinstance(value, Mapping) else {}
    user = _mapping(raw.get("user")) or raw
    profile = _mapping(raw.get("profile")) or _mapping(user.get("profile"))
    author_id = _first_text(user, "id", "userId", "user_id")
    username = _first_text(user, "username", "slug", "name")
    if not username:
        return None
    name = _first_text(user, "name", "displayName", "username")
    bio = _first_text(profile, "bio", "description") or _first_text(user, "bio", "description")
    avatar = _iwara_image_url(user.get("avatar")) or _iwara_image_url(profile.get("avatar"))
    stats = _mapping(raw.get("stats"))
    video_count = _as_int(
        user.get(
            "videoCount",
            user.get(
                "numVideos",
                profile.get("videoCount", profile.get("numVideos", stats.get("videoCount", 0))),
            ),
        )
    )
    source_url = _first_text(raw, "source_url", "url")
    if not source_url:
        source_url = f"https://www.iwara.tv/profile/{quote(username)}"
    return SearchAuthor(
        author_id=author_id or username,
        username=username,
        name=name or username,
        avatar_url=avatar,
        bio=bio,
        video_count=video_count,
        source_url=source_url,
        raw=raw,
    )


def build_video_query_params(filters: SearchFilters, page: int = 0) -> dict[str, str]:
    """Build conservative ``/videos`` parameters understood by current API."""

    params = {
        "page": str(max(0, int(page))),
        "limit": str(max(1, min(100, int(filters.page_size or 32)))),
        "sort": str(filters.sort or "date"),
    }
    if filters.rating:
        params["rating"] = filters.rating
    include_tags = split_search_terms(" ".join(filters.include_tags))
    if include_tags:
        params["tags"] = ",".join(include_tags)
    keyword = str(filters.keyword or "").strip()
    if keyword:
        # ``q`` is supported by some API deployments.  Local filtering below
        # remains authoritative when a deployment silently ignores it.
        params["q"] = keyword
    return params


def _contains_terms(video: SearchVideo, terms: tuple[str, ...], mode: str = "all") -> bool:
    if not terms:
        return True
    haystack = " ".join(
        (
            video.title,
            video.author_username,
            video.author_name,
            video.video_id,
            *video.tags,
            *video.origins,
            *video.characters,
        )
    ).casefold()
    checks = [term.casefold() in haystack for term in terms]
    return any(checks) if mode == "any" else all(checks)


def filter_videos(videos: list[SearchVideo], filters: SearchFilters) -> list[SearchVideo]:
    """Apply enhanced local filtering to normalized videos."""

    keyword_terms = split_search_terms(filters.keyword)
    include_tags = {tag.casefold() for tag in filters.include_tags if tag}
    exclude_tags = {tag.casefold() for tag in filters.exclude_tags if tag}
    include_tags.update(tag.casefold() for tag in filters.tag_all if tag)
    tag_any = {tag.casefold() for tag in filters.tag_any if tag}
    exclude_tags.update(tag.casefold() for tag in filters.tag_not if tag)
    author_terms = {term.casefold() for term in filters.author_any if term}
    if filters.author.strip():
        author_terms.add(filters.author.strip().casefold())
    author_not = {term.casefold() for term in filters.author_not if term}
    origin_any = {term.casefold() for term in filters.origin_any if term}
    origin_not = {term.casefold() for term in filters.origin_not if term}
    character_any = {term.casefold() for term in filters.character_any if term}
    character_not = {term.casefold() for term in filters.character_not if term}
    result: list[SearchVideo] = []
    for video in videos:
        video_tags = {tag.casefold() for tag in video.tags}
        origins = {value.casefold() for value in video.origins}
        characters = {value.casefold() for value in video.characters}
        author_text = (video.author_username + " " + video.author_name).casefold()
        if not _contains_terms(video, keyword_terms, filters.title_mode):
            continue
        if author_terms and not any(term in author_text for term in author_terms):
            continue
        if any(term in author_text for term in author_not):
            continue
        if origin_any and not any(term in " ".join(origins) for term in origin_any):
            continue
        if any(term in " ".join(origins) for term in origin_not):
            continue
        if character_any and not any(term in " ".join(characters) for term in character_any):
            continue
        if any(term in " ".join(characters) for term in character_not):
            continue
        if include_tags and not all(
            any(term in tag for tag in video_tags) for term in include_tags
        ):
            continue
        if tag_any and not any(
            term in tag for term in tag_any for tag in video_tags
        ):
            continue
        if any(term in tag for term in exclude_tags for tag in video_tags):
            continue
        if filters.min_views is not None and video.views < filters.min_views:
            continue
        if filters.max_views is not None and video.views > filters.max_views:
            continue
        if filters.min_likes is not None and video.likes < filters.min_likes:
            continue
        if filters.max_likes is not None and video.likes > filters.max_likes:
            continue
        if filters.min_duration is not None and video.duration < filters.min_duration:
            continue
        if filters.max_duration is not None and video.duration > filters.max_duration:
            continue
        published = parse_date(video.published_at)
        if filters.date_from and (published is None or published < filters.date_from):
            continue
        if filters.date_to and (published is None or published > filters.date_to):
            continue
        result.append(video)
    return result


def sort_videos(videos: list[SearchVideo], sort: str = "date") -> list[SearchVideo]:
    """Apply a stable local fallback sort after server filtering."""

    mode = str(sort or "date").casefold()
    if mode in {"views", "view", "most_viewed"}:
        key = lambda video: (video.views, video.likes, video.published_at)
    elif mode in {"likes", "like", "favorite", "favorites"}:
        key = lambda video: (video.likes, video.views, video.published_at)
    elif mode in {"popular", "popularity", "trending"}:
        key = lambda video: (video.views + video.likes * 20, video.published_at)
    else:
        key = lambda video: video.published_at
    return sorted(videos, key=key, reverse=True)
