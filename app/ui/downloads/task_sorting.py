"""Filter and sort the task table."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QTimer, Qt

from qfluentwidgets import FluentIcon

from ...core.models import DownloadTask, TaskStatus, status_label
from ...i18n import tr
from .task_statuses import _ACTIVE_STATUSES, _DEFAULT_STATUS_PRIORITY
from .task_text import _video_url


class TaskSortingMixin:
    """Filter and sort the task table."""

    def _passes_filters(self, task: DownloadTask) -> bool:
        state_idx = self._state_combo.currentIndex() if hasattr(self, "_state_combo") else 0
        if state_idx == 1 and task.status not in _ACTIVE_STATUSES:
            return False
        if state_idx == 2 and task.status not in (TaskStatus.QUEUED_META, TaskStatus.QUEUED_DOWNLOAD):
            return False
        if state_idx == 3 and task.status != TaskStatus.FAILED:
            return False
        if state_idx == 4 and task.status != TaskStatus.COMPLETED:
            return False
        if state_idx == 5 and task.status != TaskStatus.CANCELLED:
            return False
        if state_idx == 6 and task.status != TaskStatus.SKIPPED:
            return False

        query = self._search_edit.text().strip().lower() if hasattr(self, "_search_edit") else ""
        if query and query not in self._task_search_text(task):
            return False
        return True

    def _sort_tasks(self, tasks: list[DownloadTask]) -> list[DownloadTask]:
        def text(value: Any) -> str:
            return str(value or "").lower()

        def key(task: DownloadTask):
            order = self._task_order.get(task.task_id, 0)
            progress = self._progress_ratio(task)
            if self._sort_column == self._SORT_DEFAULT:
                return (_DEFAULT_STATUS_PRIORITY.get(task.status, 99), -task.priority, order)
            if self._sort_column == self._SORT_ADDED:
                return order
            if self._sort_column == self._COL_STATE:
                return (_DEFAULT_STATUS_PRIORITY.get(task.status, 99), text(status_label(task.status)), order)
            if self._sort_column == self._COL_TITLE:
                return (text(task.title or task.video_id), order)
            if self._sort_column == self._COL_AUTHOR:
                return (text(task.author), order)
            if self._sort_column == self._COL_PROGRESS:
                return (progress, order)
            if self._sort_column == self._COL_SIZE:
                return (task.total_bytes or task.downloaded_bytes or 0, order)
            if self._sort_column == self._COL_SPEED:
                return (text(task.speed_str), order)
            if self._sort_column == self._COL_QUALITY:
                return (text(task.quality), order)
            if self._sort_column == self._COL_PRIORITY:
                return (task.priority, order)
            if self._sort_column == self._COL_URL:
                return (text(_video_url(task.video_id)), order)
            if self._sort_column == self._COL_ID:
                return (text(task.video_id), order)
            return (_DEFAULT_STATUS_PRIORITY.get(task.status, 99), -task.priority, order)

        return sorted(tasks, key=key, reverse=self._sort_reverse)

    def _on_sort_combo_changed(self, index: int):
        mapping = {
            0: self._SORT_DEFAULT,
            1: self._COL_STATE,
            2: self._COL_TITLE,
            3: self._COL_AUTHOR,
            4: self._COL_PROGRESS,
            5: self._COL_SIZE,
            6: self._SORT_ADDED,
            7: self._COL_PRIORITY,
            8: self._COL_QUALITY,
            9: self._COL_ID,
        }
        self._sort_column = mapping.get(index, self._SORT_DEFAULT)
        if index in (4, 5, 7):
            self._sort_reverse = True
        elif index == 0:
            self._sort_reverse = False
        self._update_sort_button()
        self._restore_sort_indicator()
        self._apply_filters()

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
                self._COL_PROGRESS,
                self._COL_SIZE,
                self._COL_PRIORITY,
            )
        self._sync_sort_combo()
        self._update_sort_button()
        self._restore_sort_indicator()
        self._apply_filters()

    def _toggle_sort_direction(self):
        self._sort_reverse = not self._sort_reverse
        self._update_sort_button()
        self._restore_sort_indicator()
        self._apply_filters()

    def _sortable_columns(self) -> set[int]:
        return {
            self._COL_STATE,
            self._COL_TITLE,
            self._COL_AUTHOR,
            self._COL_PROGRESS,
            self._COL_SIZE,
            self._COL_SPEED,
            self._COL_QUALITY,
            self._COL_PRIORITY,
            self._COL_URL,
            self._COL_ID,
        }

    def _restore_sort_indicator(self):
        column = self._COL_STATE if self._sort_column in (self._SORT_DEFAULT, self._SORT_ADDED) else self._sort_column
        self._table.horizontalHeader().setSortIndicator(
            column,
            Qt.SortOrder.DescendingOrder if self._sort_reverse else Qt.SortOrder.AscendingOrder,
        )

    def _sync_sort_combo(self):
        mapping = {
            self._COL_STATE: 1,
            self._COL_TITLE: 2,
            self._COL_AUTHOR: 3,
            self._COL_PROGRESS: 4,
            self._COL_SIZE: 5,
            self._SORT_ADDED: 6,
            self._COL_PRIORITY: 7,
            self._COL_QUALITY: 8,
            self._COL_ID: 9,
        }
        self._sort_combo.blockSignals(True)
        self._sort_combo.setCurrentIndex(mapping.get(self._sort_column, 0))
        self._sort_combo.blockSignals(False)

    def _update_sort_button(self):
        self._sort_dir_btn.setIcon(FluentIcon.DOWN if self._sort_reverse else FluentIcon.UP)
        self._sort_dir_btn.setToolTip(
            tr("Descending", "降序", "降順") if self._sort_reverse else tr("Ascending", "升序", "昇順")
        )
