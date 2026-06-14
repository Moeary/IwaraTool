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

    def add(tag: str, value: Any, attrs: dict[str, str] | None = None):
        text = str(value or "").strip()
        if not text:
            return None
        node = ET.SubElement(root, tag, attrs or {})
        node.text = text
        return node

    add("title", title)
    add("originaltitle", title)
    add("sorttitle", title)
    add("id", video_id)
    add("uniqueid", video_id, {"type": "iwara", "default": "true"})
    add("iwaraid", video_id)
    add("source", "iwara")
    add("source_url", getattr(task, "url", ""))
    add("slug", getattr(task, "slug", ""))
    add("quality", getattr(task, "quality", ""))

    author = str(getattr(task, "author", "") or "")
    if author:
        add("director", author)
        add("studio", author)

    if date_only:
        add("premiered", date_only)
        add("releasedate", date_only)
        add("year", date_only[:4])
    add("published_at", getattr(task, "published_at", ""))

    runtime = duration_minutes(int(getattr(task, "duration", 0) or 0))
    if runtime:
        add("runtime", runtime)

    add("mpaa", getattr(task, "rating", ""))
    if author_message:
        add("plot", author_message)
        add("outline", author_message.splitlines()[0][:240])

    thumb_path = _thumbnail_ref(task)
    if thumb_path:
        add("thumb", thumb_path, {"aspect": "poster"})

    for tag in tags:
        add("genre", tag)
    for tag in tags:
        add("tag", tag)

    duration_seconds = int(getattr(task, "duration", 0) or 0)
    if duration_seconds > 0:
        fileinfo = ET.SubElement(root, "fileinfo")
        streamdetails = ET.SubElement(fileinfo, "streamdetails")
        video = ET.SubElement(streamdetails, "video")
        duration = ET.SubElement(video, "durationinseconds")
        duration.text = str(duration_seconds)

    add("iwara_likes", getattr(task, "likes", 0))
    add("iwara_views", getattr(task, "views", 0))
    add("iwara_comments", getattr(task, "comments", 0))

    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    xml_body = ET.tostring(root, encoding="unicode", short_empty_elements=False)
    return "<?xml version=\"1.0\" encoding=\"utf-8\" standalone=\"yes\"?>\n" + xml_body + "\n"


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
