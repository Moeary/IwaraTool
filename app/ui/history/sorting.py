"""Sort the history table by clicked column."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QTimer, Qt

from ...presentation.history import library


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
            self._sort_reverse = self._column_key(column) in library.DESCENDING_FIRST
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

    def _column_key(self, column: int) -> str:
        """The shared sort key (``library.SORT_COLUMNS``) of a table column."""
        return library.SORT_COLUMNS[column] if 0 <= column < len(library.SORT_COLUMNS) else "downloaded"

    def _column_of(self, key: str) -> int:
        return library.SORT_COLUMNS.index(key)

    def _sort_records(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return library.sort_records(
            records, self._column_key(self._sort_column), self._sort_reverse, state_of=self._record_state,
        )
