"""Derive a history record's state (on disk, moved, missing) and its search text."""

from __future__ import annotations

import os
from typing import Any

from ...config import app_config
from ...i18n import tr


def _date_only(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text[:10] if len(text) >= 10 else text


def _video_url(video_id: str) -> str:
    video_id = str(video_id or "").strip()
    return f"https://www.iwara.tv/video/{video_id}" if video_id else ""


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

    def _record_search_text(self, record: dict[str, Any], field_idx: int) -> str:
        state, _, state_detail = self._record_state(record)
        fields = {
            1: [state, state_detail],
            2: [record.get("title", "")],
            3: [record.get("author", "")],
            4: [record.get("quality", "")],
            5: [record.get("published_at", ""), record.get("downloaded_at", "")],
            6: [record.get("video_id", "")],
            7: [record.get("source_url", ""), record.get("video_id", "")],
            8: [record.get("file_path", "")],
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
            ]
        return " ".join(str(v or "") for v in values).lower()
