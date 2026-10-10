"""Fill and update the task table rows and the summary line."""

from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QTableWidgetItem

from ...core.models import DownloadTask, TaskStatus, status_label
from ...i18n import tr
from ..theme import link_color, summary_text, task_status_color
from ..ui_state import fit_table_last_column
from ..table_column_dialog import open_table_column_dialog
from .page_dependencies import download_manager
from .task_statuses import _ACTIVE_STATUSES
from .task_text import _video_url


class TaskRowsMixin:
    """Fill and update the task table rows and the summary line."""

    def _configure_columns(self):
        open_table_column_dialog(
            self._table,
            "task_table",
            title=tr("Task Columns", "任务列表字段", "タスク列設定"),
            parent=self,
        )
        fit_table_last_column(self._table)

    def _schedule_refresh(self, delay_ms: int = 80):
        if self._refresh_pending:
            return
        self._refresh_pending = True
        QTimer.singleShot(delay_ms, self._refresh_tasks)

    def _refresh_tasks(self):
        self._refresh_pending = False
        tasks = download_manager.get_tasks()
        for task in tasks:
            self._ensure_order(task.task_id)
        self._tasks_by_id = {task.task_id: task for task in tasks}
        self._apply_filters()

    def _apply_filters(self, *_args):
        tasks = list(self._tasks_by_id.values())
        visible = [task for task in tasks if self._passes_filters(task)]
        visible = self._sort_tasks(visible)
        self._render_table(visible)
        self._update_summary(tasks, visible)

    def _render_table(self, tasks: list[DownloadTask]):
        self._table.setUpdatesEnabled(False)
        try:
            self._row_by_task_id = {}
            self._visible_task_ids = [task.task_id for task in tasks]
            self._table.setRowCount(len(tasks))
            for row_idx, task in enumerate(tasks):
                self._row_by_task_id[task.task_id] = row_idx
                self._update_row(row_idx, task)
        finally:
            self._table.setUpdatesEnabled(True)

    def _update_row(self, row_idx: int, task: DownloadTask):
        values = [
            status_label(task.status),
            task.title or task.video_id,
            task.author,
            self._progress_text(task),
            self._size_text(task),
            task.speed_str,
            task.quality,
            self._priority_text(task.priority),
            _video_url(task.video_id),
            task.video_id,
        ]

        for col_idx, value in enumerate(values):
            item = self._table.item(row_idx, col_idx)
            if item is None:
                item = QTableWidgetItem()
                self._table.setItem(row_idx, col_idx, item)
            item.setText(value)
            item.setData(Qt.ItemDataRole.UserRole, task.task_id)
            item.setToolTip(self._cell_tooltip(task, col_idx, value))
            if col_idx == self._COL_STATE:
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item.setForeground(task_status_color(task.status))
            elif col_idx in (self._COL_PROGRESS, self._COL_SIZE, self._COL_SPEED):
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            elif col_idx == self._COL_PRIORITY:
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            else:
                item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        action_key, action_text, action_tip, action_enabled = self._primary_action(task)
        task_url = _video_url(task.video_id)
        self._set_action_item(
            row_idx,
            self._COL_URL,
            task.task_id,
            "open_url",
            tr("Open", "打开", "開く"),
            task_url or tr("No video URL", "没有视频链接", "動画URLがありません"),
            bool(task_url),
            action_url=task_url,
        )
        self._set_action_item(
            row_idx,
            self._COL_ACTION,
            task.task_id,
            action_key,
            action_text,
            action_tip,
            action_enabled,
        )
        self._set_action_item(
            row_idx,
            self._COL_REMOVE,
            task.task_id,
            "remove",
            tr("Remove", "移除", "削除"),
            tr("Remove task", "移除任务", "タスクを削除"),
            True,
        )

    def _set_action_item(
        self,
        row: int,
        column: int,
        task_id: str,
        action: str,
        text: str,
        tooltip: str,
        enabled: bool,
        *,
        action_url: str = "",
    ):
        item = self._table.item(row, column)
        if item is None:
            item = QTableWidgetItem()
            self._table.setItem(row, column, item)
        item.setText(text if enabled else "—")
        item.setData(Qt.ItemDataRole.UserRole, task_id)
        item.setData(Qt.ItemDataRole.UserRole + 1, action if enabled else "")
        item.setData(Qt.ItemDataRole.UserRole + 2, action_url if enabled else "")
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        item.setToolTip(tooltip)
        item.setForeground(link_color(enabled))

    def _update_summary(self, tasks: list[DownloadTask], visible: list[DownloadTask]):
        self._update_total_speed()
        active = sum(1 for task in tasks if task.status in _ACTIVE_STATUSES)
        queued = sum(1 for task in tasks if task.status in (TaskStatus.QUEUED_META, TaskStatus.QUEUED_DOWNLOAD))
        failed = sum(1 for task in tasks if task.status == TaskStatus.FAILED)
        cancelled = sum(1 for task in tasks if task.status == TaskStatus.CANCELLED)
        skipped = sum(1 for task in tasks if task.status == TaskStatus.SKIPPED)
        completed = sum(1 for task in tasks if task.status == TaskStatus.COMPLETED)
        self._summary_label.setText(
            summary_text(
                [
                    (tr("Tasks", "任务", "タスク"), len(tasks)),
                    (tr("Visible", "当前显示", "表示"), len(visible)),
                    (tr("Active", "进行中", "実行中"), active),
                    (tr("Queued", "排队", "待機"), queued),
                    (tr("Failed", "失败", "失敗"), failed),
                    (tr("Cancelled", "中断", "中断"), cancelled),
                    (tr("Skipped", "跳过", "スキップ"), skipped),
                    (tr("Completed", "完成", "完了"), completed),
                ]
            )
        )

    def _update_current_summary(self):
        tasks = list(self._tasks_by_id.values())
        visible = [
            self._tasks_by_id[task_id]
            for task_id in self._visible_task_ids
            if task_id in self._tasks_by_id
        ]
        self._update_summary(tasks, visible)

    def _task_from_info(self, info: dict) -> DownloadTask:
        task_id = str(info.get("task_id", "") or "")
        video_id = str(info.get("video_id", "") or task_id)
        status_value = str(info.get("status", TaskStatus.QUEUED_META.value) or TaskStatus.QUEUED_META.value)
        try:
            status = TaskStatus(status_value)
        except ValueError:
            status = TaskStatus.QUEUED_META
        return DownloadTask(
            task_id=task_id,
            url=f"https://www.iwara.tv/video/{video_id}",
            video_id=video_id,
            title=str(info.get("title", "") or video_id),
            author=str(info.get("author", "") or ""),
            status=status,
            priority=int(info.get("priority", 0) or 0),
        )

    def _fetch_task(self, task_id: str, info: dict | None = None) -> DownloadTask | None:
        task = download_manager.get_task(task_id)
        if task:
            return task
        if info is not None:
            return self._task_from_info(info)
        return self._tasks_by_id.get(task_id)

    def _append_visible_tasks(self, tasks: list[DownloadTask]):
        visible = [
            task
            for task in tasks
            if task.task_id not in self._row_by_task_id and self._passes_filters(task)
        ]
        if not visible:
            self._update_current_summary()
            return
        start = self._table.rowCount()
        self._table.setUpdatesEnabled(False)
        try:
            self._table.setRowCount(start + len(visible))
            for offset, task in enumerate(visible):
                row = start + offset
                self._visible_task_ids.append(task.task_id)
                self._row_by_task_id[task.task_id] = row
                self._update_row(row, task)
        finally:
            self._table.setUpdatesEnabled(True)
        self._update_current_summary()

    def _upsert_task_row(self, task: DownloadTask):
        row = self._row_by_task_id.get(task.task_id)
        visible = self._passes_filters(task)
        if visible and row is not None:
            self._update_row(row, task)
        elif visible:
            self._append_visible_tasks([task])
            return
        elif row is not None:
            self._remove_visible_task_ids([task.task_id])
            return
        self._update_current_summary()

    def _remove_visible_task_ids(self, task_ids: list[str]):
        remove_set = {task_id for task_id in task_ids if task_id}
        if not remove_set:
            return
        rows = sorted(
            (row for task_id, row in self._row_by_task_id.items() if task_id in remove_set),
            reverse=True,
        )
        if rows:
            self._table.setUpdatesEnabled(False)
            try:
                for row in rows:
                    self._table.removeRow(row)
            finally:
                self._table.setUpdatesEnabled(True)
        self._visible_task_ids = [task_id for task_id in self._visible_task_ids if task_id not in remove_set]
        self._rebuild_row_map()
        self._update_current_summary()

    def _rebuild_row_map(self):
        self._row_by_task_id = {task_id: row for row, task_id in enumerate(self._visible_task_ids)}

    def _ensure_order(self, task_id: str):
        if task_id in self._task_order:
            return
        self._task_order[task_id] = self._next_order
        self._next_order += 1
