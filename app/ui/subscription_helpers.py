"""Pure formatting, parsing, and sorting helpers for subscriptions."""
from __future__ import annotations

import re
import webbrowser
from typing import Any
from urllib.parse import urlparse

from PySide6.QtGui import QColor
from qfluentwidgets import isDarkTheme

from ..core.models import TaskStatus, status_label
from ..i18n import tr


def _split_title_keywords(value: str) -> list[str]:
    terms: list[str] = []
    for part in str(value or "").replace(";", ",").replace("\n", ",").split(","):
        term = part.strip().casefold()
        if term and term not in terms:
            terms.append(term)
    return terms


def _title_matcher(query: str, *, regex_mode: bool):
    """Build a title predicate for the transient subscription-list search."""
    if regex_mode:
        pattern = re.compile(query, re.IGNORECASE)
        return lambda title: bool(pattern.search(str(title or "")))
    needle = str(query or "").casefold()
    return lambda title: needle in str(title or "").casefold()


def _title_matches_keywords(title: str, include_terms: list[str], exclude_terms: list[str]) -> bool:
    haystack = str(title or "").casefold()
    if include_terms and not any(term in haystack for term in include_terms):
        return False
    return not any(term in haystack for term in exclude_terms)


def _ellipsize(value: str, limit: int) -> str:
    value = str(value or "")
    if len(value) <= limit:
        return value
    return value[: max(1, limit - 1)].rstrip() + "…"


def _source_type_label(source_type: str) -> str:
    if source_type == "feed":
        return tr("Feed", "账号流", "フィード")
    if source_type == "author":
        return tr("Author", "作者", "作者")
    if source_type == "playlist":
        return tr("Playlist", "播放列表", "リスト")
    return source_type


def _source_origin_label(source: dict[str, Any]) -> str:
    origin = str(source.get("source_origin", "") or "").strip().casefold()
    source_type = str(source.get("source_type", "") or "").strip().casefold()
    if origin == "oreno3d":
        return tr("Oreno3D author", "Oreno3D 作者", "Oreno3D作者")
    if origin == "account" or source_type == "feed":
        return tr("Iwara account", "Iwara 账户订阅", "Iwaraアカウント")
    if origin == "playlist" or source_type == "playlist":
        return tr("Subscription list", "订阅列表", "購読リスト")
    return tr("Local author", "本地作者", "ローカル作者")


def _source_origin_color(source: dict[str, Any]) -> str:
    origin = str(source.get("source_origin", "") or "").strip().casefold()
    source_type = str(source.get("source_type", "") or "").strip().casefold()
    if isDarkTheme():
        if origin == "oreno3d":
            return "#ffb86c"
        if origin == "account" or source_type == "feed":
            return "#4cc2ff"
        if origin == "playlist" or source_type == "playlist":
            return "#c3a6ff"
        return "#6bdc7a"
    if origin == "oreno3d":
        return "#d97706"
    if origin == "account" or source_type == "feed":
        return "#0078d4"
    if origin == "playlist" or source_type == "playlist":
        return "#8764b8"
    return "#107c10"


def _parse_ui_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


def _source_sort_label(field: str) -> str:
    labels = {
        "title": tr("Display Name", "作者名", "表示名"),
        "created_at": tr("Import Time", "导入时间", "取込日時"),
        "last_checked_at": tr("Last Check", "上次刷新", "最終確認"),
        "new_count": tr("New", "新增", "新規"),
        "undownloaded_count": tr("Missing", "未下载", "未保存"),
        "item_count": tr("Items", "项目", "項目"),
        "source_origin": tr("Subscription Source", "订阅来源", "購読元"),
        "source_key": tr("Username", "用户名", "ユーザー名"),
    }
    return labels.get(str(field or "").strip().casefold(), str(field or ""))


def _source_sort_key(source: dict[str, Any], field: str = "title") -> tuple[Any, ...]:
    """Return a stable key for source-table field sorting.

    ``field`` is intentionally data-oriented so the UI combo can persist a
    compact value without coupling the table to translated labels.
    """

    source_key = str(source.get("source_key", "") or "").casefold()
    source_type = str(source.get("source_type", "") or "").casefold()
    field = str(field or "title").strip().casefold()
    if field == "title":
        title = str(source.get("title", "") or "").casefold()
        return (title or source_key, source_key, source_type)
    if field in {"new_count", "undownloaded_count", "item_count"}:
        try:
            numeric = int(source.get(field, 0) or 0)
        except (TypeError, ValueError):
            numeric = 0
        return (numeric, source_key, source_type)
    value = str(source.get(field, "") or "").casefold()
    return (value, source_key, source_type)


def _source_search_text(source: dict[str, Any]) -> str:
    values = [
        source.get("title", ""),
        source.get("source_key", ""),
        source.get("source_type", ""),
        source.get("source_origin", ""),
        _source_type_label(str(source.get("source_type", "") or "")),
        _source_origin_label(source),
        source.get("created_at", ""),
        source.get("last_checked_at", ""),
        _source_url(source),
    ]
    return " ".join(str(value or "") for value in values).casefold()


def _item_state_text(
    *,
    downloaded: bool,
    queued: bool,
    file_exists: bool,
    task_status: str = "",
    download_state: str = "",
    download_reason: str = "",
) -> str:
    if downloaded and file_exists:
        return tr("Downloaded", "已下载", "保存済み")
    if downloaded:
        return tr("Moved", "已移走", "移動済み")
    if download_state or download_reason:
        return tr("Not Downloadable", "不可下载", "保存不可")
    status = _task_status_from_value(task_status)
    if status:
        return status_label(status)
    if queued:
        return tr("Queued", "已入队", "キュー内")
    return tr("Ready", "可下载", "保存可能")


def _state_color(
    *,
    downloaded: bool,
    queued: bool,
    file_exists: bool,
    task_status: str = "",
    download_state: str = "",
) -> QColor:
    if downloaded and file_exists:
        return QColor("#107c10")
    if downloaded:
        return QColor("#c17d00")
    if download_state:
        return QColor("#c42b1c")
    status = _task_status_from_value(task_status)
    if status in (TaskStatus.DOWNLOADING, TaskStatus.COMPLETED):
        return QColor("#107c10")
    if status in (TaskStatus.RESOLVING, TaskStatus.QUEUED_META, TaskStatus.QUEUED_DOWNLOAD):
        return QColor("#0078d4")
    if status in (TaskStatus.CANCELLING, TaskStatus.SKIPPED):
        return QColor("#c17d00")
    if status == TaskStatus.FAILED:
        return QColor("#c42b1c")
    if status == TaskStatus.CANCELLED:
        return QColor("#666666")
    if queued:
        return QColor("#0078d4")
    return QColor("#555555")


def _item_not_downloadable(item: dict[str, Any]) -> bool:
    return bool(str(item.get("download_state", "") or ""))


def _task_status_from_value(value: str) -> TaskStatus | None:
    try:
        return TaskStatus(str(value or ""))
    except ValueError:
        return None


def _item_date_key(item: dict[str, Any]) -> str:
    return str(
        item.get("published_at", "")
        or item.get("discovered_at", "")
        or item.get("updated_at", "")
        or ""
    )


def _extract_author_key(text: str) -> str:
    parsed = _parse_iwara_path(text)
    if parsed:
        parts = parsed
        for key in ("user", "profile"):
            if key in parts:
                idx = parts.index(key)
                if idx + 1 < len(parts):
                    return parts[idx + 1]
    return text.strip().strip("/")


def _extract_playlist_key(text: str) -> str:
    parsed = _parse_iwara_path(text)
    if parsed and "playlist" in parsed:
        idx = parsed.index("playlist")
        if idx + 1 < len(parsed):
            return parsed[idx + 1]
    return text.strip().strip("/")


def _detect_source_input(text: str) -> tuple[str, str]:
    lowered = text.strip().lower()
    for prefix in ("playlist:", "list:"):
        if lowered.startswith(prefix):
            return "playlist", text.split(":", 1)[1].strip().strip("/")
    parsed = _parse_iwara_path(text)
    if parsed and "playlist" in parsed:
        return "playlist", _extract_playlist_key(text)
    return "author", _extract_author_key(text)


def _source_url(source: dict[str, Any]) -> str:
    source_type = str(source.get("source_type", "") or "")
    key = str(source.get("source_key", "") or "").strip()
    durable_url = str(source.get("source_url", "") or "").strip()
    if source_type == "author" and durable_url:
        return durable_url
    if source_type == "author" and key:
        return f"https://www.iwara.tv/profile/{key}"
    if source_type == "playlist" and key:
        return f"https://www.iwara.tv/playlist/{key}"
    if source_type == "feed":
        return "https://www.iwara.tv/subscriptions"
    return ""


def _video_url(video_id: str) -> str:
    video_id = str(video_id or "").strip()
    return f"https://www.iwara.tv/video/{video_id}" if video_id else ""


def _open_url(url: str):
    if url:
        webbrowser.open(url)


def _parse_iwara_path(text: str) -> list[str] | None:
    if "iwara.tv" not in text:
        return None
    normalized = text if "://" in text else f"https://{text.lstrip('/')}"
    try:
        parsed = urlparse(normalized)
    except Exception:
        return None
    return [part for part in parsed.path.split("/") if part]


def _date_only(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text[:10] if len(text) >= 10 else text
