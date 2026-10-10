"""Follow task events from the download manager: added, status, progress, removed."""

from __future__ import annotations

from PySide6.QtCore import QTimer

from ...core.download.speed_format import format_speed, parse_speed
from ...core.models import DownloadTask, TaskStatus
from ...i18n import tr


class TaskLiveUpdatesMixin:
    """Follow task events from the download manager: added, status, progress, removed."""

    def _on_task_added(self, task_id: str, _info: dict):
        if task_id in self._tasks_by_id:
            return
        self._ensure_order(task_id)
        info = dict(_info or {})
        info["task_id"] = task_id
        task = self._fetch_task(task_id, info)
        if task:
            self._tasks_by_id[task_id] = task
            self._append_visible_tasks([task])

    def _on_tasks_added(self, infos: list):
        added: list[DownloadTask] = []
        for info in infos:
            task_id = str(info.get("task_id", "") or "")
            if task_id:
                self._ensure_order(task_id)
                if task_id in self._tasks_by_id:
                    continue
                task = self._fetch_task(task_id, info)
                if task:
                    self._tasks_by_id[task_id] = task
                    added.append(task)
        self._append_visible_tasks(added)

    def _on_task_status_changed(self, task_id: str, _status_str: str):
        self._ensure_order(task_id)
        task = self._fetch_task(task_id)
        if not task:
            return
        self._tasks_by_id[task_id] = task
        self._upsert_task_row(task)
        if self._sort_column in (self._SORT_DEFAULT, self._COL_STATE):
            self._schedule_refresh(300)

    def _on_task_priority_changed(self, task_id: str, _priority: int):
        task = self._fetch_task(task_id)
        if not task:
            return
        self._tasks_by_id[task_id] = task
        self._upsert_task_row(task)
        if self._sort_column in (self._SORT_DEFAULT, self._COL_PRIORITY):
            self._schedule_refresh(0)

    def _on_task_progress(self, task_id: str, _downloaded: int, _total: int, _speed: str):
        self._ensure_order(task_id)
        task = self._tasks_by_id.get(task_id)
        if task is None:
            task = self._fetch_task(task_id)
        if task:
            self._tasks_by_id[task_id] = task
            task.downloaded_bytes = _downloaded
            task.total_bytes = _total
            task.speed_str = _speed
        self._pending_progress_ids.add(task_id)
        if self._progress_flush_pending:
            return
        self._progress_flush_pending = True
        QTimer.singleShot(180, self._flush_progress_updates)

    def _on_task_error(self, task_id: str, _message: str):
        self._ensure_order(task_id)
        task = self._fetch_task(task_id)
        if task:
            self._tasks_by_id[task_id] = task
            self._upsert_task_row(task)

    def _on_task_removed(self, task_id: str):
        self._tasks_by_id.pop(task_id, None)
        self._remove_visible_task_ids([task_id])

    def _on_tasks_removed(self, task_ids: list):
        changed = False
        clean_ids: list[str] = []
        for raw_task_id in task_ids:
            task_id = str(raw_task_id or "")
            if not task_id:
                continue
            clean_ids.append(task_id)
            if self._tasks_by_id.pop(task_id, None) is not None:
                changed = True
        if changed:
            self._remove_visible_task_ids(clean_ids)

    def _update_total_speed(self):
        """Sum the live speed of every task that is actually downloading."""

        total = 0.0
        downloading = 0
        for task in self._tasks_by_id.values():
            if task.status != TaskStatus.DOWNLOADING:
                continue
            downloading += 1
            total += parse_speed(task.speed_str)
        self._total_speed_label.setText(format_speed(total))
        self._speed_hint.setText(
            tr(
                f"{downloading} downloading",
                f"{downloading} 个任务正在下载",
                f"{downloading} 件ダウンロード中",
            )
            if downloading
            else tr("Idle", "当前空闲", "待機中")
        )

    def _flush_progress_updates(self):
        self._progress_flush_pending = False
        ids = list(self._pending_progress_ids)
        self._pending_progress_ids.clear()
        self._update_total_speed()
        if self._sort_column in (self._COL_PROGRESS, self._COL_SIZE, self._COL_SPEED):
            self._schedule_refresh(120)
            return
        for task_id in ids:
            self._update_progress_cells(task_id)

    def _update_progress_cells(self, task_id: str):
        task = self._tasks_by_id.get(task_id)
        row = self._row_by_task_id.get(task_id)
        if not task or row is None or row < 0 or row >= self._table.rowCount():
            return
        updates = {
            self._COL_PROGRESS: self._progress_text(task),
            self._COL_SIZE: self._size_text(task),
            self._COL_SPEED: task.speed_str,
        }
        for column, value in updates.items():
            item = self._table.item(row, column)
            if not item:
                continue
            item.setText(value)
            item.setToolTip(self._cell_tooltip(task, column, value))
