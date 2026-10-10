"""Sort the history table by clicked column."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QTimer, Qt

from .record_state import _video_url


class HistorySortingMixin:
    """Sort the history table by clicked column."""

    def _on_header_clicked(self, column: int):
        if column not in self._sortable_columns():
            self._restore_sort_indicator()
            QTimer.singleShot(0, self._restore_sort_indicator)
            return
        if column == self._sort_column:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_column = column
            self._sort_reverse = column in (
                self._COL_PUBLISHED,
                self._COL_LIKES,
                self._COL_VIEWS,
                self._COL_DOWNLOADED,
            )
        self._restore_sort_indicator()
        self._apply_filters()

    def _restore_sort_indicator(self):
        self._table.horizontalHeader().setSortIndicator(
            self._sort_column,
            Qt.SortOrder.DescendingOrder if self._sort_reverse else Qt.SortOrder.AscendingOrder,
        )

    def _sortable_columns(self) -> set[int]:
        return {
            self._COL_STATE,
            self._COL_TITLE,
            self._COL_AUTHOR,
            self._COL_QUALITY,
            self._COL_PUBLISHED,
            self._COL_LIKES,
            self._COL_VIEWS,
            self._COL_DOWNLOADED,
            self._COL_ID,
            self._COL_PATH,
            self._COL_SOURCE_URL,
        }

    def _sort_records(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        def text(value: Any) -> str:
            return str(value or "").lower()

        def number(value: Any) -> int:
            try:
                return int(value or 0)
            except Exception:
                return 0

        def key(record: dict[str, Any]):
            state, state_key, _ = self._record_state(record)
            if self._sort_column == self._COL_STATE:
                return (0 if state_key == "ok" else 1, text(state))
            if self._sort_column == self._COL_TITLE:
                return text(record.get("title") or record.get("video_id"))
            if self._sort_column == self._COL_AUTHOR:
                return text(record.get("author"))
            if self._sort_column == self._COL_QUALITY:
                return text(record.get("quality"))
            if self._sort_column == self._COL_PUBLISHED:
                return text(record.get("published_at"))
            if self._sort_column == self._COL_LIKES:
                return number(record.get("likes"))
            if self._sort_column == self._COL_VIEWS:
                return number(record.get("views"))
            if self._sort_column == self._COL_DOWNLOADED:
                return text(record.get("downloaded_at"))
            if self._sort_column == self._COL_ID:
                return text(record.get("video_id"))
            if self._sort_column == self._COL_PATH:
                return text(record.get("file_path"))
            if self._sort_column == self._COL_SOURCE_URL:
                return text(record.get("source_url") or _video_url(record.get("video_id", "")))
            return text(record.get("downloaded_at"))

        return sorted(records, key=key, reverse=self._sort_reverse)
