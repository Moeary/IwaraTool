"""Work out a search result's Iwara video and author identity."""

from __future__ import annotations

import re

from ...core.search import SearchAuthor, SearchVideo


def _extract_iwara_video_id(value: str) -> str:
    match = re.search(r"/video/([^/?#]+)", str(value or ""))
    return match.group(1) if match else ""


def _oreno3d_video_url(video: SearchVideo) -> str:
    """Return the original Oreno3D URL even after bridge hydration mutates the card."""

    raw = video.raw if isinstance(video.raw, dict) else {}
    return str(
        raw.get("oreno3d_url")
        or (video.source_url if video.source_kind == "oreno3d" else "")
        or ""
    ).strip()


AuthorTarget = tuple[str, str, str, str]


def _field_text(candidate: object, *keys: str) -> str:
    if isinstance(candidate, dict):
        for key in keys:
            raw_value = candidate.get(key)
            if isinstance(raw_value, str) and raw_value.strip():
                return raw_value.strip()
    return ""


def _author_key(candidate: object) -> str:
    text = str(candidate or "").strip()
    if not text:
        return ""
    # Search adapters may return a profile URL instead of a username.
    # Keep the last profile segment so the subscription store receives the
    # same key as a manually entered author subscription.
    match = re.search(r"/(?:profile|user|author)/([^/?#]+)", text, re.IGNORECASE)
    if match:
        text = match.group(1)
    return text.lstrip("@").strip().strip("/")


def _author_subscription_target(
    value: SearchAuthor | SearchVideo | object,
) -> AuthorTarget | None:
    """Return ``(username, title, remote_id, avatar_url)`` for one result."""

    if isinstance(value, SearchAuthor):
        return _author_result_target(value)
    if isinstance(value, SearchVideo):
        return _video_author_target(value)
    return None


def _author_result_target(value: SearchAuthor) -> AuthorTarget | None:
    raw = value.raw if isinstance(value.raw, dict) else {}
    raw_profile = raw.get("profile") if isinstance(raw.get("profile"), dict) else {}
    username = _author_key(
        value.username
        or _field_text(raw, "username", "slug", "author_username")
        or _field_text(raw_profile, "username", "slug")
    )
    if not username:
        return None
    return (
        username,
        str(value.name or _field_text(raw, "name", "displayName") or username).strip() or username,
        str(value.author_id or _field_text(raw, "id", "userId", "author_id")).strip(),
        str(value.avatar_url or _field_text(raw, "avatar_url", "avatar", "image")).strip(),
    )


def _mapped_oreno_author_target(raw: dict) -> AuthorTarget | None:
    # Oreno author resolution may use a surviving work on the author page
    # rather than the selected (possibly deleted) Iwara video.  Keep that
    # mapping independent from the bridge video's hydration flag.
    mapped_author = raw.get("oreno_iwara_author")
    if not isinstance(mapped_author, dict):
        return None
    username = _author_key(
        mapped_author.get("username")
        or mapped_author.get("slug")
        or mapped_author.get("profile_url")
    )
    if not username:
        return None
    return (
        username,
        str(mapped_author.get("name") or username).strip() or username,
        str(mapped_author.get("id") or "").strip(),
        str(mapped_author.get("avatar_url") or "").strip(),
    )


def _video_author_target(value: SearchVideo) -> AuthorTarget | None:
    raw = value.raw if isinstance(value.raw, dict) else {}
    mapped = _mapped_oreno_author_target(raw)
    if mapped is not None:
        return mapped
    # An Oreno3D card carries Oreno's uploader name, which is not an Iwara
    # account.  Do not turn that bridge-side label into a local Iwara source
    # until the linked Iwara video metadata has been hydrated.
    if raw.get("oreno3d_url") and raw.get("_iwara_metadata_loaded") is not True:
        return None
    raw_user = raw.get("user") if isinstance(raw.get("user"), dict) else {}
    raw_author = raw.get("author") if isinstance(raw.get("author"), dict) else {}
    bridge_metadata_loaded = bool(
        raw.get("oreno3d_url") and raw.get("_iwara_metadata_loaded") is True
    )
    username = _video_author_username(value, raw, raw_user, raw_author, bridge_metadata_loaded)
    if not username:
        return None
    if bridge_metadata_loaded:
        title = str(
            _field_text(raw_user, "name", "displayName")
            or _field_text(raw_author, "name", "displayName")
            or _field_text(raw, "author_name")
            or username
        ).strip() or username
    else:
        title = str(
            value.author_name
            or _field_text(raw_user, "name", "displayName")
            or _field_text(raw_author, "name", "displayName")
            or _field_text(raw, "author_name", "author")
            or username
        ).strip() or username
    remote_id = str(
        _field_text(raw_user, "id", "userId", "author_id")
        or _field_text(raw_author, "id", "userId", "author_id")
        or _field_text(raw, "user_id", "author_id")
    ).strip()
    avatar_url = str(
        _field_text(raw_user, "avatar_url", "avatar", "image")
        or _field_text(raw_author, "avatar_url", "avatar", "image")
        or _field_text(raw, "avatar_url", "author_avatar")
    ).strip()
    return username, title, remote_id, avatar_url


def _video_author_username(
    value: SearchVideo, raw: dict, raw_user: dict, raw_author: dict, bridge_metadata_loaded: bool
) -> str:
    if bridge_metadata_loaded:
        # Once the bridge has been hydrated, only fields from the Iwara
        # payload may identify the account.  Falling back to the Oreno card's
        # uploader here would silently subscribe the wrong site user.
        return _author_key(
            _field_text(raw_user, "username", "slug", "handle")
            or _field_text(raw_author, "username", "slug", "handle")
            or _field_text(raw, "username", "slug", "author_username")
        )
    return _author_key(
        value.author_username
        or _field_text(raw_user, "username", "slug", "handle")
        or _field_text(raw_author, "username", "slug", "handle")
        or _field_text(raw, "username", "slug", "author_username", "author_url")
        or value.author_name
        or _field_text(raw, "author_name", "author")
    )


def _author_source_info(value: SearchAuthor | SearchVideo | object) -> tuple[str, str]:
    """Return a durable author page URL and its source origin for one result."""

    if isinstance(value, SearchAuthor):
        return str(value.source_url or "").strip(), "iwara"
    if isinstance(value, SearchVideo):
        raw = value.raw if isinstance(value.raw, dict) else {}
        if value.source_kind == "oreno3d" or raw.get("oreno3d_url"):
            return str(raw.get("oreno3d_author_url") or "").strip(), "oreno3d"
    return "", ""
