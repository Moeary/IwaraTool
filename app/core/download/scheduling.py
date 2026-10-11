"""Start queued tasks within the concurrency limit, schedule window and speed limit."""

from __future__ import annotations

from datetime import datetime

from ...config import app_config
from ...i18n import tr
from ...signal_bus import signal_bus
from .policy import is_time_in_window
from ..models import TaskStatus


class SchedulingMixin:
    """Start queued tasks within the concurrency limit, schedule window and speed limit."""

    def _count_active(self) -> int:
        """Must be called with self._lock held."""
        return len(self._active_task_ids)

    def _try_activate(self):
        """Promote QUEUED_META tasks into RESOLVING up to the concurrency limit."""
        if not self._download_schedule_allows_start():
            return
        to_resolve: list[str] = []
        with self._lock:
            if self._shutting_down:
                return
            active = self._count_active()
            limit = app_config.max_concurrent
            while active < limit and self._queued_meta_ids:
                task_id = self._pop_next_queued_task_locked()
                if not task_id:
                    break
                task = self._tasks.get(task_id)
                if not task or task.status != TaskStatus.QUEUED_META:
                    continue
                task.status = TaskStatus.RESOLVING
                self._active_task_ids.add(task.task_id)
                self._touch_task_activity_locked(task.task_id)
                to_resolve.append(task.task_id)
                active += 1

        for tid in to_resolve:
            signal_bus.task_status_changed.emit(tid, TaskStatus.RESOLVING.value)
            self._resolve_executor.submit(self._resolve_task, tid)

    def _pop_next_queued_task_locked(self) -> str:
        """Pop the oldest valid task among the highest queued priority."""
        best_task_id = ""
        best_priority = -101
        stale: list[str] = []
        for task_id in self._queued_meta_ids:
            task = self._tasks.get(task_id)
            if task is None or task.status != TaskStatus.QUEUED_META:
                stale.append(task_id)
                continue
            if task.priority > best_priority:
                best_task_id = task_id
                best_priority = task.priority
        for task_id in stale:
            try:
                self._queued_meta_ids.remove(task_id)
            except ValueError:
                pass
        if best_task_id:
            self._queued_meta_ids.remove(best_task_id)
        return best_task_id

    @staticmethod
    def _download_schedule_allows_start() -> bool:
        if not app_config.download_schedule_enabled:
            return True
        return is_time_in_window(
            datetime.now().time(),
            app_config.download_schedule_start,
            app_config.download_schedule_end,
        )

    @staticmethod
    def _global_speed_limit_bytes() -> int:
        if not app_config.global_speed_limit_enabled:
            return 0
        return max(0, int(app_config.global_speed_limit_kib)) * 1024

    def _start_downloading(self, task_id: str):
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            if task.cancel_requested:
                task.status = TaskStatus.CANCELLING
                should_cancel = True
            else:
                task.status = TaskStatus.DOWNLOADING
                self._touch_task_activity_locked(task_id)
                should_cancel = False
        if should_cancel:
            self._cancel_task_terminal(
                task_id,
                tr("Cancelled by user", "用户已中断", "ユーザーが中断しました"),
            )
            return
        signal_bus.task_status_changed.emit(task_id, TaskStatus.DOWNLOADING.value)
        self._schedule_task_persist()
        self._download_executor.submit(self._download_task, task_id)
