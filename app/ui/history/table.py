"""Filter and render history records into the table, in batches."""

from __future__ import annotations

import time
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTableWidgetItem

from ...core.download.locations import resolve_record_location
from ...i18n import tr
from ..style.theme import link_color, qcolor, summary_text
from ..common.ui_state import fit_table_last_column
from ..common.table_column_dialog import open_table_column_dialog
from ...presentation.history import library
from ...presentation.history.record_state import _date_only, _video_url


class HistoryTableMixin:
    """Filter and render history records into the table, in batches."""

    def _configure_columns(self):
        open_table_column_dialog(
            self._table,
            "history_table",
            title=tr("History Columns", "历史列表字段", "履歴列設定"),
            parent=self,
        )
        fit_table_last_column(self._table)

    def _apply_filters(self, *_args):
        self._filter_timer.stop()
        # One disk check per record for the whole pass (filter, sort and render
        # all ask for each record's state).
        self._state_memo = {}
        query = self._search_edit.text().strip().lower() if hasattr(self, "_search_edit") else ""
        field_idx = self._field_combo.currentIndex() if hasattr(self, "_field_combo") else 0
        state_idx = self._state_combo.currentIndex() if hasattr(self, "_state_combo") else 0

        visible_records, counts = library.filter_records(
            self._all_records,
            state_of=self._record_state,
            query=query,
            field=field_idx,
            state_filter=state_idx,
            scope=self._scope,
            author=self._author_filter,
        )
        visible_records = self._sort_records(visible_records)
        self._visible_records = visible_records
        self._render_current_view(visible_records)
        self._summary_label.setText(summary_text(library.summary_parts(counts)))

    def _render_table(self, records: list[dict[str, Any]]):
        self._render_timer.stop()
        self._render_records = records
        self._render_next = 0
        self._render_deferred = False
        self._table.setUpdatesEnabled(False)
        try:
            self._table.clearContents()
            self._table.setRowCount(len(records))
        finally:
            self._table.setUpdatesEnabled(True)
        if len(records) > self.RENDER_SYNC_ROWS and not self.isVisible():
            self._render_deferred = True  # filled in showEvent
            self._update_action_state()
            return
        self._render_rows(sliced=len(records) > self.RENDER_SYNC_ROWS)

    def _render_rows(self, *, sliced: bool):
        records = self._render_records
        labels = {
            "page": tr("Page", "页面", "ページ"),
            "page_tip": tr("Open video page", "打开视频页面", "動画ページを開く"),
            "folder": tr("Folder", "文件夹", "フォルダー"),
            "folder_tip": tr("Open folder", "打开文件夹", "フォルダーを開く"),
            "file": tr("File", "文件", "ファイル"),
            "file_tip": tr("Open file", "打开文件", "ファイルを開く"),
            "rename": tr("Rename", "重命名", "名前を変更"),
            "rename_tip": tr("Rename file", "重命名文件", "ファイル名を変更"),
            "delete": tr("Delete", "删除", "削除"),
            "delete_tip": tr("Remove DB record", "删除数据库记录", "DB履歴を削除"),
            "no_url": tr("No video URL", "没有视频链接", "動画URLがありません"),
        }
        start = time.perf_counter()
        self._table.setUpdatesEnabled(False)
        try:
            while self._render_next < len(records):
                row_idx = self._render_next
                self._render_next += 1
                self._render_record_row(row_idx, records[row_idx], labels)
                if sliced and time.perf_counter() - start > self.RENDER_SLICE_SECONDS:
                    break
        finally:
            self._table.setUpdatesEnabled(True)
        if self._render_next < len(records):
            self._render_timer.start(0)
        else:
            self._state_memo = None  # the pass is over; later checks look at the disk again
        self._update_action_state()

    def _render_record_row(self, row_idx: int, record: dict, labels: dict[str, str]):
        state, state_key, state_detail, file_exists = self._record_state_full(record)
        video_id = str(record.get("video_id", "") or "")
        remote = state_key == "remote"
        file_path = (
            f"[aria2] {record.get('remote_path', '') or ''}" if remote else str(record.get("file_path", "") or "")
        )
        playable = file_exists or (remote and bool(resolve_record_location(record).stream_url))
        values = [
            state,
            str(record.get("title", "") or video_id),
            str(record.get("author", "") or ""),
            str(record.get("quality", "") or ""),
            _date_only(str(record.get("published_at", "") or "")),
            str(record.get("likes", 0) or 0),
            str(record.get("views", 0) or 0),
            str(record.get("downloaded_at", "") or ""),
            video_id,
            file_path,
        ]

        for col_idx, value in enumerate(values):
            item = QTableWidgetItem(value)
            if col_idx == self._COL_STATE:
                item.setToolTip(state_detail)
            elif col_idx in self._LONG_COLUMNS:
                item.setToolTip(value)  # short columns need no tooltip (3000 rows x 16 cells add up)
            if col_idx == self._COL_STATE:
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item.setForeground(qcolor({"ok": "success", "remote": "info"}.get(state_key, "warning")))
            elif col_idx in (self._COL_LIKES, self._COL_VIEWS):
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._table.setItem(row_idx, col_idx, item)

        source_url = str(record.get("source_url", "") or _video_url(video_id))
        source_item = QTableWidgetItem(source_url)
        source_item.setToolTip(source_url or labels["no_url"])
        self._table.setItem(row_idx, self._COL_SOURCE_URL, source_item)
        self._set_action_item(
            row_idx, self._COL_OPEN_URL, labels["page"], labels["page_tip"], "open_url",
            bool(video_id), video_id, action_url=_video_url(video_id),
        )
        self._set_action_item(
            row_idx, self._COL_OPEN_FOLDER, labels["folder"], labels["folder_tip"], "folder",
            file_exists, video_id,
        )
        self._set_action_item(
            row_idx, self._COL_OPEN_FILE, labels["file"], labels["file_tip"], "file",
            playable, video_id,
        )
        self._set_action_item(
            row_idx, self._COL_RENAME, labels["rename"], labels["rename_tip"], "rename",
            file_exists and not remote, video_id,
        )
        self._set_action_item(
            row_idx, self._COL_REMOVE, labels["delete"], labels["delete_tip"], "remove",
            bool(video_id), video_id,
        )

    def _continue_render(self):
        if self._render_next < len(self._render_records):
            self._render_rows(sliced=True)

    def _set_action_item(
        self,
        row: int,
        column: int,
        text: str,
        tooltip: str,
        action: str,
        enabled: bool,
        video_id: str,
        *,
        action_url: str = "",
    ):
        item = QTableWidgetItem(text if enabled else "-")
        if enabled:
            item.setToolTip(tooltip)
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        item.setForeground(link_color(enabled))
        self._table.setItem(row, column, item)
