"""The table of scanned files, their target names and the summary line."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTableWidgetItem

from ...i18n import tr
from ..theme import qcolor, summary_text
from .page_dependencies import download_manager


class RepairResultsTableMixin:
    """The table of scanned files, their target names and the summary line."""

    def _render_all_items(self):
        self._table.setUpdatesEnabled(False)
        try:
            self._clear_table()
            self._table.setRowCount(len(self._items))
            for row, item in enumerate(self._items):
                self._render_item(item, row=row)
        finally:
            self._table.setUpdatesEnabled(True)
            self._table.viewport().update()
        self._update_summary()

    def _clear_table(self):
        self._path_to_row.clear()
        self._table.clearContents()
        self._table.setRowCount(0)

    def _render_item(self, item: dict[str, Any], *, row: int | None = None):
        path = str(item.get("path", "") or "")
        if not path:
            return
        if row is None:
            row = self._path_to_row.get(path)
        if row is None:
            row = self._table.rowCount()
            self._table.insertRow(row)
        self._path_to_row[path] = row
        state_key = str(item.get("status", "") or "")
        if state_key == "ready" and item.get("target_conflict"):
            state_key = "conflict"
        state_text = {
            "ready": tr("Ready", "待修复", "待機"),
            "completed": tr("Done", "已完成", "完了"),
            "not_found": tr("Skip", "跳过", "スキップ"),
            "ambiguous": tr("Check", "待确认", "要確認"),
            "failed": tr("Failed", "失败", "失敗"),
            "conflict": tr("Conflict", "冲突", "競合"),
            "skipped": tr("Skip", "跳过", "スキップ"),
        }.get(state_key, state_key or tr("Unknown", "未知", "不明"))
        values = [
            state_text,
            path,
            str(item.get("video_id", "") or ""),
            str(item.get("title", "") or ""),
            str(item.get("published_at", "") or "")[:10],
            str(item.get("target_path", "") or ""),
            str(item.get("target_conflict", "") or item.get("message", "") or ""),
        ]
        color = {
            "ready": qcolor("accent"),
            "completed": qcolor("success"),
            "not_found": qcolor("warning"),
            "ambiguous": qcolor("warning"),
            "failed": qcolor("danger"),
            "conflict": qcolor("danger"),
            "skipped": qcolor("neutral"),
        }.get(state_key)
        for column, value in enumerate(values):
            cell = self._table.item(row, column) or QTableWidgetItem()
            cell.setText(value)
            cell.setToolTip(value)
            cell.setData(Qt.ItemDataRole.UserRole, path)
            if column == self._COL_STATE:
                cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if color:
                    cell.setForeground(color)
            self._table.setItem(row, column, cell)
        target_path = str(item.get("target_path", "") or "")
        if target_path:
            self._path_to_row.setdefault(target_path, row)

    def _refresh_target_previews(self, *_args):
        if self._worker is not None and self._worker_mode == "repair":
            return
        self._items = download_manager.preview_repair_files(self._items, options=self._options())
        self._items_by_path = {
            str(item.get("path", "") or ""): item for item in self._items
            if str(item.get("path", "") or "")
        }
        self._table.setUpdatesEnabled(False)
        try:
            for item in self._items:
                if item.get("status") != "ready":
                    continue
                self._render_item(item)
        finally:
            self._table.setUpdatesEnabled(True)
            self._table.viewport().update()
        if self._worker is None:
            self._start_btn.setEnabled(any(item.get("status") == "ready" and not item.get("target_conflict") for item in self._items))
        self._update_summary()

    def _update_summary(self):
        counts: dict[str, int] = {}
        for item in self._items:
            key = "conflict" if item.get("status") == "ready" and item.get("target_conflict") else str(item.get("status", "") or "unknown")
            counts[key] = counts.get(key, 0) + 1
        skipped = counts.get("not_found", 0) + counts.get("ambiguous", 0) + counts.get("skipped", 0)
        self._summary_label.setText(
            summary_text(
                [
                    (tr("Total", "共", "合計"), len(self._items)),
                    (tr("Ready", "待修复", "待機"), counts.get("ready", 0)),
                    (tr("Done", "已完成", "完了"), counts.get("completed", 0)),
                    (tr("Skipped", "跳过", "スキップ"), skipped),
                    (
                        tr("Conflict/failed", "冲突/失败", "競合/失敗"),
                        counts.get("conflict", 0) + counts.get("failed", 0),
                    ),
                ]
            )
        )
