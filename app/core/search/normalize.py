"""Turn raw Iwara / Oreno3D API records into search result models."""

from __future__ import annotations

import re
from typing import Any, Mapping
from urllib.parse import quote, urlparse

from .filtering import split_search_terms
from .models import IWARA_IMAGE_SOURCE_KIND, SearchAuthor, SearchPlaylist, SearchVideo


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


def small_cover_url(url: str) -> str:
    """Swap a video's full-size ``thumbnail-NN.jpg`` for the ~10 KB rendition.

    Home cards show dozens of covers at once, so the small file keeps the page
    quick.  Anything that is not a generated video thumbnail is returned as is.
    """

    text = str(url or "")
    if "/image/original/" in text and re.search(r"/thumbnail-\d+\.jpg$", text):
        return text.replace("/image/original/", "/image/thumbnail/", 1)
    return text


def avatar_url(user: Any) -> str:
    """Avatar image URL of an Iwara ``user`` object, or ``""``."""

    return _iwara_image_url(_mapping(user).get("avatar"), variant="avatar")


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
        joined_at=_first_text(user, "createdAt"),
        raw=raw,
    )


def _iwara_file_thumbnail(value: Any) -> str:
    """Use the small ``thumbnail`` rendition of an uploaded image file."""

    image = _mapping(value)
    image_id = _first_text(image, "id", "fileId")
    if not image_id:
        return _iwara_image_url(value)
    return f"https://i.iwara.tv/image/thumbnail/{quote(image_id)}/{quote(image_id)}.jpg"


def normalize_image(value: Mapping[str, Any] | Any) -> SearchVideo | None:
    """Normalize an Iwara image post into a browse-only search card.

    Image posts share the video card layout but have no video file, so they
    are never queueable; the card opens the post on iwara.tv instead.
    """

    image = dict(value) if isinstance(value, Mapping) else {}
    normalized = normalize_video(image)
    if normalized is None:
        return None
    image_id = normalized.video_id
    normalized.source_url = f"https://www.iwara.tv/image/{quote(image_id)}"
    normalized.iwara_url = ""
    normalized.source_kind = IWARA_IMAGE_SOURCE_KIND
    normalized.downloadable = False
    normalized.duration = 0.0
    normalized.thumbnail_url = _iwara_file_thumbnail(image.get("thumbnail"))
    return normalized


def normalize_playlist(value: Mapping[str, Any] | Any) -> SearchPlaylist | None:
    """Normalize one ``/search?type=playlists`` row."""

    raw = dict(value) if isinstance(value, Mapping) else {}
    playlist_id = _first_text(raw, "id", "playlistId")
    if not playlist_id:
        return None
    user = _mapping(raw.get("user"))
    thumbnail = _mapping(raw.get("thumbnail"))
    file_info = _mapping(thumbnail.get("file"))
    file_id = _first_text(file_info, "id")
    thumbnail_url = ""
    if file_id:
        index = max(0, _as_int(thumbnail.get("thumbnail"), 0))
        thumbnail_url = (
            f"https://i.iwara.tv/image/thumbnail/{quote(file_id)}/thumbnail-{index:02d}.jpg"
        )
    return SearchPlaylist(
        playlist_id=playlist_id,
        title=_first_text(raw, "title", "name") or playlist_id,
        video_count=_as_int(raw.get("numVideos", raw.get("videoCount", 0))),
        author_username=_first_text(user, "username"),
        author_name=_first_text(user, "name", "username"),
        thumbnail_url=thumbnail_url,
        source_url=f"https://www.iwara.tv/playlist/{quote(playlist_id)}",
        raw=raw,
    )
