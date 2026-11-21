"""The table of scanned files, their target names and the summary line."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTableWidgetItem

from ...core.repair.matching import parse_manual_video_id
from ...i18n import tr
from ..style.theme import qcolor, summary_text
from .worker import RepairPreviewWorker, RepairWorker

# Rows whose Iwara ID the user may type in (a finished repair is final).
_MANUAL_ID_STATES = frozenset({"not_found", "ambiguous", "failed", "ready", "conflict", "skipped", ""})
_MANUAL_ID_HINT_STATES = frozenset({"not_found", "ambiguous"})


class RepairResultsTableMixin:
    """The table of scanned files, their target names and the summary line."""

    def _render_all_items(self):
        # Existing rows and cells are reused; ``_render_item`` skips rows
        # whose text is unchanged, so a re-sorted or re-planned list of
        # thousands of files does not rebuild every cell.
        self._state_styles = self._build_state_styles()
        self._table.setUpdatesEnabled(False)
        try:
            self._path_to_row.clear()
            self._table.setRowCount(len(self._items))
            for row in [row for row in self._row_signatures if row >= len(self._items)]:
                del self._row_signatures[row]
            for row, item in enumerate(self._items):
                self._render_item(item, row=row)
        finally:
            self._table.setUpdatesEnabled(True)
            self._table.viewport().update()
        self._update_summary()

    def _render_items(self, items: list[dict[str, Any]]):
        self._state_styles = self._build_state_styles()
        self._table.setUpdatesEnabled(False)
        try:
            for item in items:
                self._render_item(item)
        finally:
            self._table.setUpdatesEnabled(True)
            self._table.viewport().update()

    def _clear_table(self):
        self._path_to_row.clear()
        self._row_signatures.clear()
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
        target_path = str(item.get("target_path", "") or "")
        if target_path:
            self._path_to_row.setdefault(target_path, row)
        state_key = str(item.get("status", "") or "")
        if state_key == "ready" and item.get("target_conflict"):
            state_key = "conflict"
        styles = self._state_styles or self._build_state_styles()
        state_text, color = styles.get(state_key, (state_key or styles[""][0], None))
        detail = str(item.get("target_conflict", "") or item.get("message", "") or "")
        editable = state_key in _MANUAL_ID_STATES
        if state_key in _MANUAL_ID_HINT_STATES:
            detail = f"{detail} · {self._manual_id_hint()}" if detail else self._manual_id_hint()
        values = [
            state_text,
            path,
            str(item.get("video_id", "") or ""),
            str(item.get("title", "") or ""),
            str(item.get("published_at", "") or "")[:10],
            str(item.get("target_path", "") or ""),
            detail,
        ]
        signature = (state_key, *values)
        if self._row_signatures.get(row) == signature:
            return
        self._row_signatures[row] = signature
        base_flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        # Filling cells must not look like a user edit of the ID column.
        signals_blocked = self._table.blockSignals(True)
        try:
            for column, value in enumerate(values):
                cell = self._table.item(row, column)
                if cell is None:
                    cell = QTableWidgetItem()
                    self._table.setItem(row, column, cell)
                cell.setText(value)
                cell.setData(Qt.ItemDataRole.UserRole, path)
                if column == self._COL_ID:
                    cell.setFlags(base_flags | Qt.ItemFlag.ItemIsEditable if editable else base_flags)
                    cell.setToolTip(
                        "\n".join(part for part in (value, self._manual_id_hint()) if part)
                        if editable else value
                    )
                else:
                    cell.setFlags(base_flags)
                    cell.setToolTip(value)
                if column == self._COL_STATE:
                    cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    if color:
                        cell.setForeground(color)
        finally:
            self._table.blockSignals(signals_blocked)

    @staticmethod
    def _manual_id_hint() -> str:
        return tr(
            "Double-click the Iwara ID cell to enter an ID or URL",
            "可双击 Iwara ID 列手动填写 ID 或链接",
            "Iwara ID 欄をダブルクリックして ID または URL を入力できます",
        )

    def _restore_row(self, path: str):
        """Re-render ``path``'s row from its item, discarding typed-in cell text."""

        row = self._path_to_row.get(path)
        item = self._items_by_path.get(path)
        if row is None or item is None:
            return
        self._row_signatures.pop(row, None)
        self._render_item(item, row=row)

    def _on_table_item_changed(self, cell: QTableWidgetItem):
        """Look up a file again by the Iwara ID the user typed into its row."""

        if cell.column() != self._COL_ID:
            return
        path = str(cell.data(Qt.ItemDataRole.UserRole) or "")
        item = self._items_by_path.get(path)
        if item is None:
            return
        text = cell.text().strip()
        if text == str(item.get("video_id", "") or "").strip():
            return
        if not text or (self._worker is not None and self._worker.isRunning()):
            self._restore_row(path)
            return
        if not parse_manual_video_id(text):
            self._restore_row(path)
            self._show_warning(
                tr("Invalid Iwara ID", "Iwara ID 无效", "無効な Iwara ID"),
                tr(
                    "Enter an Iwara video ID or a video link such as https://www.iwara.tv/video/<id>.",
                    "请输入 Iwara 视频 ID，或 https://www.iwara.tv/video/<id> 这样的视频链接。",
                    "Iwara 動画 ID、または https://www.iwara.tv/video/<id> のような動画リンクを入力してください。",
                ),
            )
            return
        self._append_log(
            tr(
                f"[Repair] looking up {path} by Iwara ID {text}",
                f"[修复] 按 Iwara ID {text} 重新查询：{path}",
                f"[修復] Iwara ID {text} で再検索: {path}",
            )
        )
        self._status_label.setText(tr("Looking up the video…", "正在查询视频…", "動画を検索中…"))
        self._start_worker(
            RepairWorker(
                "resolve",
                items=list(self._items),
                options=self._options(),
                path=path,
                video_id_text=text,
                parent=self,
            )
        )

    @staticmethod
    def _build_state_styles() -> dict[str, tuple[str, Any]]:
        """State key -> (label, colour), built once per batch of rows.

        ``tr`` reads the language from the settings store under a lock; per
        cell, while scan threads also translate, that cost seconds per batch.
        """

        return {
            "": (tr("Unknown", "未知", "不明"), None),
            "ready": (tr("Ready", "待修复", "待機"), qcolor("accent")),
            "completed": (tr("Done", "已完成", "完了"), qcolor("success")),
            "not_found": (tr("Skip", "跳过", "スキップ"), qcolor("warning")),
            "ambiguous": (tr("Check", "待确认", "要確認"), qcolor("warning")),
            "failed": (tr("Failed", "失败", "失敗"), qcolor("danger")),
            "conflict": (tr("Conflict", "冲突", "競合"), qcolor("danger")),
            "skipped": (tr("Skip", "跳过", "スキップ"), qcolor("neutral")),
        }

    def _set_items(self, items: list[dict[str, Any]]):
        self._items = [dict(item) for item in items if isinstance(item, dict)]
        self._items_by_path = {
            str(item.get("path", "") or ""): item
            for item in self._items
            if str(item.get("path", "") or "")
        }

    def _refresh_target_previews(self, *_args):
        """Re-plan targets after an option change, debounced and off the GUI thread."""

        self._preview_timer.start()

    def _preview_pending(self) -> bool:
        return self._preview_timer.isActive() or self._preview_worker is not None

    def _run_target_preview(self):
        if self._worker is not None:
            # A running scan plans its own result; a repair keeps its plan.
            return
        if not self._items:
            self._update_start_button()
            self._update_summary()
            return
        if self._preview_worker is not None:
            self._preview_dirty = True
            return
        self._preview_generation += 1
        worker = RepairPreviewWorker(self._preview_generation, self._items, self._options(), parent=self)
        self._preview_worker = worker
        worker.result_ready.connect(self._on_preview_result)
        worker.error.connect(self._on_worker_error)
        worker.finished.connect(self._on_preview_finished)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _on_preview_result(self, generation: int, items: object):
        if generation != self._preview_generation or self._worker is not None or not isinstance(items, list):
            return
        self._set_items(items)
        self._render_items([item for item in self._items if item.get("status") == "ready"])
        self._update_start_button()
        self._update_summary()

    def _on_preview_finished(self):
        self._preview_worker = None
        if self._preview_dirty:
            self._preview_dirty = False
            self._run_target_preview()

    def _update_start_button(self):
        if self._worker is None:
            self._start_btn.setEnabled(any(
                item.get("status") == "ready" and not item.get("target_conflict")
                for item in self._items
            ))

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
