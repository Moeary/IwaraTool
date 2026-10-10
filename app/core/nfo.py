"""Kodi/Jellyfin-compatible movie NFO generation helpers."""
from __future__ import annotations

import json
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any


def parse_tags(tags_json: str) -> list[str]:
    if not tags_json:
        return []
    try:
        data = json.loads(tags_json)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    tags: list[str] = []
    seen: set[str] = set()
    for item in data:
        if isinstance(item, dict):
            tag_text = str(
                item.get("name")
                or item.get("title")
                or item.get("id")
                or item.get("type")
                or item.get("slug")
                or ""
            ).strip()
        else:
            tag_text = str(item or "").strip()
        normalized = tag_text.lower()
        if tag_text and normalized not in seen:
            tags.append(tag_text)
            seen.add(normalized)
    return tags


def build_nfo_text(task: Any, tags: list[str]) -> str:
    author_message = _extract_author_message(str(getattr(task, "raw_json", "") or ""))
    date_only = _extract_date_text(str(getattr(task, "published_at", "") or ""))
    video_id = str(getattr(task, "video_id", "") or "")
    title = (str(getattr(task, "title", "") or "") or video_id).strip()
    root = ET.Element("movie")
    _add_identity_fields(root, task, title, video_id)
    _add_release_fields(root, task, date_only, author_message, tags)
    _add_runtime_and_stats(root, task)

    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    xml_body = ET.tostring(root, encoding="unicode", short_empty_elements=False)
    return "<?xml version=\"1.0\" encoding=\"utf-8\" standalone=\"yes\"?>\n" + xml_body + "\n"


def _add(root: ET.Element, tag: str, value: Any, attrs: dict[str, str] | None = None):
    """Append ``<tag>value</tag>`` unless the value is empty."""

    text = str(value or "").strip()
    if not text:
        return None
    node = ET.SubElement(root, tag, attrs or {})
    node.text = text
    return node


def _add_identity_fields(root: ET.Element, task: Any, title: str, video_id: str) -> None:
    _add(root, "title", title)
    _add(root, "originaltitle", title)
    _add(root, "sorttitle", title)
    # Keep the long-standing field names alongside the newer Iwara-specific
    # aliases.  Emby is forgiving about unknown tags, but it does not map
    # every custom name (for example ``iwara_views``) back to its standard
    # movie metadata field.
    _add(root, "video_id", video_id)
    _add(root, "id", video_id)
    _add(root, "uniqueid", video_id, {"type": "iwara", "default": "true"})
    _add(root, "iwaraid", video_id)
    _add(root, "source", "iwara")
    _add(root, "source_url", getattr(task, "url", ""))
    _add(root, "slug", getattr(task, "slug", ""))
    _add(root, "quality", getattr(task, "quality", ""))


def _add_release_fields(root: ET.Element, task: Any, date_only: str, author_message: str, tags: list[str]) -> None:
    """Author, dates, runtime, rating, plot, poster and tags."""

    author = str(getattr(task, "author", "") or "")
    if author:
        _add(root, "author", author)
        _add(root, "director", author)
        _add(root, "studio", author)

    if date_only:
        _add(root, "premiered", date_only)
        _add(root, "releasedate", date_only)
        _add(root, "year", date_only[:4])
    _add(root, "published_at", getattr(task, "published_at", ""))

    runtime = duration_minutes(int(getattr(task, "duration", 0) or 0))
    if runtime:
        _add(root, "runtime", runtime)

    rating = getattr(task, "rating", "")
    _add(root, "rating", rating)
    _add(root, "mpaa", rating)
    if author_message:
        _add(root, "plot", author_message)
        _add(root, "outline", author_message.splitlines()[0][:240])

    thumb_path = _thumbnail_ref(task)
    if thumb_path:
        _add(root, "thumb", thumb_path, {"aspect": "poster"})

    for tag in tags:
        _add(root, "genre", tag)
    for tag in tags:
        _add(root, "tag", tag)


def _add_runtime_and_stats(root: ET.Element, task: Any) -> None:
    """Stream duration, view / like / comment counts and the raw tag payload."""

    duration_seconds = int(getattr(task, "duration", 0) or 0)
    if duration_seconds > 0:
        # ``duration`` is used by the older NFO files that are already known
        # to import correctly in Emby.  Keep the precise stream detail too.
        _add(root, "duration", duration_seconds)
        fileinfo = ET.SubElement(root, "fileinfo")
        streamdetails = ET.SubElement(fileinfo, "streamdetails")
        video = ET.SubElement(streamdetails, "video")
        duration = ET.SubElement(video, "durationinseconds")
        duration.text = str(duration_seconds)

    likes = getattr(task, "likes", 0)
    views = getattr(task, "views", 0)
    comments = getattr(task, "comments", 0)
    _add(root, "likes", likes)
    _add(root, "views", views)
    _add(root, "comments", comments)
    _add(root, "iwara_likes", likes)
    _add(root, "iwara_views", views)
    _add(root, "iwara_comments", comments)

    # Preserve the source tag payload when it is available.  This is an
    # optional compatibility field; the repeated ``genre``/``tag`` nodes
    # above remain the primary Emby-friendly representation.
    raw_tags_json = str(getattr(task, "tags_json", "") or "").strip()
    if raw_tags_json:
        _add(root, "tags_json", raw_tags_json)


def duration_minutes(duration_seconds: int) -> int:
    try:
        seconds = int(duration_seconds or 0)
    except Exception:
        seconds = 0
    if seconds <= 0:
        return 0
    return max(1, (seconds + 59) // 60)


def _extract_date_text(published_at: str) -> str:
    if not published_at:
        return ""
    text = published_at.strip()
    if not text:
        return ""
    iso_text = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso_text)
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        m = re.match(r"^(\d{4}-\d{2}-\d{2})", text)
        return m.group(1) if m else ""


def _extract_author_message(raw_json: str) -> str:
    if not raw_json:
        return ""
    try:
        data = json.loads(raw_json)
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""

    candidates = [
        data.get("body"),
        data.get("description"),
        data.get("message"),
    ]

    user = data.get("user")
    if isinstance(user, dict):
        candidates.extend(
            [
                user.get("body"),
                user.get("description"),
                user.get("bio"),
                user.get("about"),
            ]
        )
        profile = user.get("profile")
        if isinstance(profile, dict):
            candidates.extend(
                [
                    profile.get("body"),
                    profile.get("description"),
                    profile.get("bio"),
                    profile.get("about"),
                ]
            )

    for value in candidates:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _thumbnail_ref(task: Any) -> str:
    thumbnail_path = str(getattr(task, "thumbnail_path", "") or "")
    if thumbnail_path and os.path.isfile(thumbnail_path):
        return os.path.basename(thumbnail_path)
    file_path = str(getattr(task, "file_path", "") or "")
    if file_path:
        candidate = os.path.splitext(file_path)[0] + ".jpg"
        if os.path.isfile(candidate):
            return os.path.basename(candidate)
    return ""
