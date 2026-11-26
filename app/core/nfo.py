"""Kodi/Jellyfin-compatible movie NFO files: writing them for downloads, reading them back for the library."""
from __future__ import annotations

import json
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
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


# ── reading an NFO back (the library's tags, people and plot) ────────────────

NFO_MAX_BYTES = 1_000_000  # a movie NFO is a few KB; anything bigger is not one


@dataclass(frozen=True)
class NfoInfo:
    """What the library shows and filters by from a Kodi / Jellyfin movie NFO."""

    title: str = ""
    plot: str = ""
    tags: tuple[str, ...] = ()  # <genre> and <tag>, first spelling kept
    people: tuple[str, ...] = ()  # <actor><name>, <director>, <studio>, <author>
    year: str = ""
    rating: str = ""


def nfo_path_for(video_path: str) -> str:
    """The NFO beside a video: ``<stem>.nfo``, else the folder's ``movie.nfo``; "" when neither exists."""

    if not video_path:
        return ""
    candidates = (os.path.splitext(video_path)[0] + ".nfo", os.path.join(os.path.dirname(video_path), "movie.nfo"))
    return next((path for path in candidates if os.path.isfile(path)), "")


def _unique(values) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text.casefold() not in seen:
            seen.add(text.casefold())
            out.append(text)
    return tuple(out)


def parse_nfo_text(text: str) -> NfoInfo | None:
    """``None`` when the text is not an NFO with a ``<movie>``-like root."""

    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None

    def first(*tags: str) -> str:
        for tag in tags:
            node = root.find(tag)
            if node is not None and (node.text or "").strip():
                return node.text.strip()
        return ""

    people = [node.findtext("name") for node in root.findall("actor")]
    people += [node.text for tag in ("director", "studio", "author") for node in root.findall(tag)]
    premiered = first("premiered", "releasedate")
    return NfoInfo(
        title=first("title", "originaltitle"),
        plot=first("plot", "outline"),
        tags=_unique(node.text for tag in ("genre", "tag") for node in root.findall(tag)),
        people=_unique(people),
        year=first("year") or premiered[:4],
        rating=first("mpaa", "rating"),
    )


def read_nfo(path: str) -> NfoInfo | None:
    try:
        if os.path.getsize(path) > NFO_MAX_BYTES:
            return None
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError:
        return None
    return parse_nfo_text(data.decode("utf-8", errors="replace").lstrip("﻿"))


_NFO_CACHE: dict[str, tuple[float, int, NfoInfo | None]] = {}


def nfo_for_video(video_path: str) -> NfoInfo | None:
    """The NFO next to ``video_path``, parsed once per change of the file (blocking: run off the UI loop)."""

    path = nfo_path_for(video_path)
    if not path:
        return None
    try:
        stat = os.stat(path)
    except OSError:
        return None
    cached = _NFO_CACHE.get(path)
    if cached and cached[0] == stat.st_mtime and cached[1] == stat.st_size:
        return cached[2]
    info = read_nfo(path)
    _NFO_CACHE[path] = (stat.st_mtime, stat.st_size, info)
    return info
