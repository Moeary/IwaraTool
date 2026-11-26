"""Derive a history record's state (on disk, moved, missing) and its search text."""

from __future__ import annotations

import os
from typing import Any

from ...config import app_config
from ...i18n import tr
from ...core import site as sites
from ...core.download.locations import record_is_remote, resolve_record_location


def _date_only(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text[:10] if len(text) >= 10 else text


def _video_url(video_id: str) -> str:
    return sites.video_url(str(video_id or "").strip())


def compute_record_state(record: dict[str, Any]) -> tuple[str, str, str, bool]:
    """``(label, key, detail, file exists)``: key is ``ok`` / ``moved`` / ``remote``."""

    if record_is_remote(record):
        location = resolve_record_location(record)
        if location.local_path:
            detail = tr(
                f"Saved on the aria2 host; reachable through the mount: {location.local_path}",
                f"保存在 aria2 远端，可通过本机挂载访问：{location.local_path}",
                f"aria2 ホストに保存。マウント経由で開けます: {location.local_path}",
            )
        elif location.stream_url:
            detail = tr(
                "Saved on the aria2 host; plays over HTTP",
                "保存在 aria2 远端，可通过 HTTP 播放",
                "aria2 ホストに保存。HTTP で再生できます",
            )
        else:
            detail = tr(
                "Saved on the aria2 host; not reachable from this machine",
                "保存在 aria2 远端，本机无法直接访问",
                "aria2 ホストに保存。この PC からは開けません",
            )
        return (
            tr("On NAS", "远端", "リモート"),
            "remote",
            f"{detail}\n{location.remote_path}",
            bool(location.local_path),
        )
    file_path = str(record.get("file_path", "") or "")
    if not file_path or not os.path.isfile(file_path):
        return (
            tr("Moved", "已移走", "移動済み"),
            "moved",
            tr("The stored file path no longer exists", "数据库中的文件路径已不存在", "保存されたファイルパスが存在しません"),
            False,
        )

    try:
        root = os.path.abspath(app_config.download_dir)
        file_abs = os.path.abspath(file_path)
        inside_root = os.path.commonpath([root, file_abs]) == root
    except Exception:
        inside_root = False

    if inside_root:
        return (
            tr("OK", "正常", "正常"),
            "ok",
            tr("File exists in the current download folder", "文件位于当前下载文件夹内", "現在の保存先内にファイルがあります"),
            True,
        )
    return (
        tr("Moved", "已移走", "移動済み"),
        "moved",
        tr("File exists, but is outside the current download folder", "文件存在，但不在当前下载文件夹内", "ファイルは存在しますが現在の保存先外です"),
        True,
    )


def record_search_text(record: dict[str, Any], field_idx: int, state: tuple[str, str, str]) -> str:
    """Lower-case text searched by the filter; ``field_idx`` follows ``library.SEARCH_FIELDS``."""

    state, _, state_detail = state[:3]
    fields = {
        1: [state, state_detail],
        2: [record.get("title", "")],
        3: [record.get("author", "")],
        4: [record.get("quality", "")],
        5: [record.get("published_at", ""), record.get("downloaded_at", "")],
        6: [record.get("video_id", "")],
        7: [record.get("source_url", ""), record.get("video_id", "")],
        8: [record.get("file_path", ""), record.get("remote_path", "")],
    }
    if field_idx in fields:
        values = fields[field_idx]
    else:
        values = [
            state,
            state_detail,
            record.get("title", ""),
            record.get("author", ""),
            record.get("quality", ""),
            record.get("published_at", ""),
            record.get("downloaded_at", ""),
            record.get("likes", ""),
            record.get("views", ""),
            record.get("video_id", ""),
            record.get("source_url", ""),
            record.get("file_path", ""),
            record.get("remote_path", ""),
        ]
    return " ".join(str(v or "") for v in values).lower()


class HistoryRecordStateMixin:
    """Derive a history record's state (on disk, moved, missing) and its search text."""

    def _record_state(self, record: dict[str, Any]) -> tuple[str, str, str]:
        return self._record_state_full(record)[:3]

    def _record_state_full(self, record: dict[str, Any]) -> tuple[str, str, str, bool]:
        """``(label, key, detail, file exists)``, memoised during one filter/render pass."""

        memo = self._state_memo
        if memo is not None:
            cached = memo.get(id(record))
            if cached is not None:
                return cached
        result = self._compute_record_state(record)
        if memo is not None:
            memo[id(record)] = result
        return result

    def _compute_record_state(self, record: dict[str, Any]) -> tuple[str, str, str, bool]:
        return compute_record_state(record)

    def _record_search_text(self, record: dict[str, Any], field_idx: int) -> str:
        return record_search_text(record, field_idx, self._record_state(record))
