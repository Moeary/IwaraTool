"""Small metadata normalization helpers shared by downloads and subscriptions."""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any
from urllib.parse import quote, urlparse

from ..i18n import tr


MAX_STORED_TEXT_CHARS = 20000
SUBSCRIPTION_UNAVAILABLE_STATE = "unavailable"


def dict_or_empty(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def iwara_image_url(image: dict[str, Any], *, variant: str = "thumbnail") -> str:
    image_id = str(image.get("id", "") or "").strip()
    name = str(image.get("name", "") or "").strip()
    variant = str(variant or "thumbnail").strip() or "thumbnail"
    if image_id and name:
        return f"https://i.iwara.tv/image/{quote(variant)}/{quote(image_id)}/{quote(name)}"
    path = str(image.get("path", "") or "").strip().strip("/")
    if path and name:
        encoded_path = "/".join(quote(part) for part in path.split("/") if part)
        return f"https://i.iwara.tv/image/{quote(variant)}/{encoded_path}/{quote(name)}"
    return ""


def clip_stored_text(value: Any, limit: int = MAX_STORED_TEXT_CHARS) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[:limit]


def copy_compact_fields(data: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key in keys:
        value = data.get(key)
        if value is None or value == "":
            continue
        if isinstance(value, str):
            compact[key] = clip_stored_text(value)
        elif isinstance(value, (int, float, bool)):
            compact[key] = value
    return compact


def compact_video_raw_json(video_info: dict[str, Any]) -> str:
    """Keep only NFO-relevant API fields instead of the full video payload."""
    if not isinstance(video_info, dict):
        return "{}"

    compact = copy_compact_fields(
        video_info,
        (
            "id",
            "title",
            "slug",
            "rating",
            "createdAt",
            "body",
            "description",
            "message",
        ),
    )

    user = dict_or_empty(video_info.get("user"))
    if user:
        compact_user = copy_compact_fields(
            user,
            ("id", "username", "name", "body", "description", "bio", "about"),
        )
        profile = dict_or_empty(user.get("profile"))
        if profile:
            compact_profile = copy_compact_fields(
                profile,
                ("body", "description", "bio", "about"),
            )
            if compact_profile:
                compact_user["profile"] = compact_profile
        if compact_user:
            compact["user"] = compact_user

    return json.dumps(compact, ensure_ascii=False, separators=(",", ":"))


def subscription_item_from_video(video: dict[str, Any]) -> dict[str, Any]:
    video_id = str(video.get("id", "") or video.get("videoId", "") or "").strip()
    user = video.get("user")
    author = ""
    if isinstance(user, dict):
        author = str(user.get("username") or user.get("name") or "").strip()
    download_state, download_reason = subscription_download_block_from_video_info(video)
    return {
        "video_id": video_id,
        "title": str(video.get("title", "") or video_id),
        "author": author,
        "published_at": str(video.get("createdAt", "") or video.get("updatedAt", "") or ""),
        "source_url": f"https://www.iwara.tv/video/{video_id}" if video_id else "",
        "thumbnail_url": subscription_thumbnail_url(video),
        "download_state": download_state,
        "download_reason": download_reason,
        "download_state_known": bool(download_state or download_reason or video.get("fileUrl")),
    }


def subscription_thumbnail_url(video: dict[str, Any]) -> str:
    custom_thumbnail = dict_or_empty(video.get("customThumbnail"))
    if custom_thumbnail:
        custom_url = iwara_image_url(custom_thumbnail, variant="original")
        if custom_url:
            return custom_url
    file_info = dict_or_empty(video.get("file"))
    file_id = str(file_info.get("id", "") or "").strip()
    host = urlparse(str(video.get("fileUrl", "") or "")).netloc
    if not file_id or not host:
        return ""
    index = max(0, int(video.get("thumbnail", 0) or 0))
    return f"https://{host}/image/original/{quote(file_id)}/thumbnail-{index:02d}.jpg"


def subscription_download_block_from_video_info(
    video_info: dict[str, Any],
) -> tuple[str, str]:
    file_url = str(video_info.get("fileUrl", "") or "")
    if file_url:
        return "", ""
    embed = str(video_info.get("embedUrl", "") or "")
    embed_lower = embed.lower()
    if "youtube" in embed_lower or "youtu.be" in embed_lower:
        return SUBSCRIPTION_UNAVAILABLE_STATE, tr(
            f"External YouTube embed; cannot be downloaded directly: {embed}",
            f"YouTube 外部嵌入视频，无法直接下载：{embed}",
            f"YouTube 外部埋め込みのため直接保存できません: {embed}",
        )
    message = str(video_info.get("message", "") or "")
    private = bool(video_info.get("private"))
    if message == "errors.privateVideo" or private:
        return SUBSCRIPTION_UNAVAILABLE_STATE, tr(
            "Private video. The current account has no permission to download it.",
            "私有作品，当前账号没有权限下载。",
            "非公開動画です。現在のアカウントには保存権限がありません。",
        )
    return "", ""


def subscription_download_block_from_error(error: str) -> tuple[str, str]:
    text = str(error or "").strip()
    lower = text.lower()
    if (
        "no permission" in lower
        or "403" in lower
        or "forbidden" in lower
        or "没有权限" in text
        or "不可见" in text
        or "私有" in text
        or "private" in lower
    ):
        return SUBSCRIPTION_UNAVAILABLE_STATE, tr(
            "The current account has no permission to view or download this video.",
            "当前账号没有权限查看或下载该作品。",
            "現在のアカウントにはこの動画を表示または保存する権限がありません。",
        )
    return "", ""


def extract_date_text(published_at: str) -> str:
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
        match = re.match(r"^(\d{4}-\d{2}-\d{2})", text)
        return match.group(1) if match else ""


def split_filter_tags(text: str) -> list[str]:
    if not text:
        return []
    parts = re.split(r"[\s,，;；|]+", text.strip())
    normalized: list[str] = []
    for part in parts:
        token = part.strip().lower().lstrip("#")
        if token and token not in normalized:
            normalized.append(token)
    return normalized


def normalize_video_tags(tags: list[Any]) -> set[str]:
    normalized: set[str] = set()
    for item in tags:
        if isinstance(item, dict):
            for key in ("id", "type", "slug", "name", "title"):
                raw = str(item.get(key, "") or "").strip().lower().lstrip("#")
                if raw:
                    normalized.add(raw)
            continue
        text = str(item or "").strip().lower().lstrip("#")
        if text:
            normalized.add(text)
    return normalized


# Compatibility aliases used by the existing manager and third-party imports.
_dict_or_empty = dict_or_empty
_iwara_image_url = iwara_image_url
_compact_video_raw_json = compact_video_raw_json
_subscription_item_from_video = subscription_item_from_video
_subscription_thumbnail_url = subscription_thumbnail_url
_subscription_download_block_from_video_info = subscription_download_block_from_video_info
_subscription_download_block_from_error = subscription_download_block_from_error
_extract_date_text = extract_date_text
_split_filter_tags = split_filter_tags
_normalize_video_tags = normalize_video_tags
