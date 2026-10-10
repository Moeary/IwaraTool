"""User commands on queued tasks: priority, retry, restore, cancel, remove, clear."""

from __future__ import annotations

from ...config import app_config
from ...i18n import tr
from ...signal_bus import signal_bus
from ..manager_state import (
    CANCEL_ORIGIN_AUTO_STALL as _CANCEL_ORIGIN_AUTO_STALL,
    CANCEL_ORIGIN_MANUAL as _CANCEL_ORIGIN_MANUAL,
    TERMINAL_STATUSES as _TERMINAL_STATUSES,
)
from ..models import DownloadTask, TaskStatus


class TaskControlMixin:
    """User commands on queued tasks: priority, retry, restore, cancel, remove, clear."""

    def get_tasks(self) -> list[DownloadTask]:
        with self._lock:
            return list(self._tasks.values())

    def get_task(self, task_id: str) -> DownloadTask | None:
        with self._lock:
            return self._tasks.get(task_id)

    def set_task_priority(self, task_id: str, priority: int) -> bool:
        """Set a persisted queue priority without pre-empting active work."""
        value = max(-100, min(100, int(priority)))
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False
            task.priority = value
        signal_bus.task_priority_changed.emit(task_id, value)
        self._schedule_task_persist()
        self._try_activate()
        return True

    def resume_scheduled_downloads(self):
        """Wake the scheduler after a configured time window opens."""
        self._try_activate()

    def apply_runtime_download_policy(self):
        """Apply mutable policies to the scheduler and an aria2 backend."""
        self._try_activate()
        if app_config.aria2_rpc_enabled:
            self._apply_aria2_global_speed_limit()

    def retry_task(self, task_id: str):
        """Re-queue a failed task."""
        temp_file_path = ""
        temp_title = ""
        with self._lock:
            task = self._tasks.get(task_id)
            if task and task.status == TaskStatus.FAILED:
                temp_file_path = task.file_path
                temp_title = task.title or task.video_id
        if not temp_title:
            return

        _, cleanup_failed = self._log_retry_temp_cleanup(temp_title, temp_file_path)
        if cleanup_failed:
            signal_bus.task_error.emit(
                task_id,
                tr(
                    "Temp cache cleanup failed; retry was not started",
                    "临时缓存清理失败，未开始重试",
                    "一時キャッシュ削除に失敗したため再試行しません",
                ),
            )
            return

        requeued = False
        with self._lock:
            task = self._tasks.get(task_id)
            if task and task.status == TaskStatus.FAILED:
                task.status = TaskStatus.QUEUED_META
                task.cancel_requested = False
                task.delete_temp_on_cancel = False
                task.remove_after_cancel = False
                task.cancel_origin = ""
                task.aria2_gid = ""
                task.error_msg = ""
                task.downloaded_bytes = 0
                task.total_bytes = 0
                task.download_url = ""
                self._unmark_terminal_locked(task_id)
                self._queued_meta_ids.append(task_id)
                requeued = True
        if not requeued:
            return
        signal_bus.task_status_changed.emit(task_id, TaskStatus.QUEUED_META.value)
        self._schedule_task_persist()
        self._try_activate()

    def restore_cancelled_task(self, task_id: str) -> bool:
        """Put a cancelled task back into the queue without deleting temp data."""
        title = ""
        requeued = False
        with self._lock:
            task = self._tasks.get(task_id)
            if not task or task.status != TaskStatus.CANCELLED:
                return False
            title = task.title or task.video_id
            self._restore_cancelled_task_locked(task)
            requeued = True

        if not requeued:
            return False
        signal_bus.log_message.emit(
            tr(
                f"[Restore] \"{title}\" re-queued",
                f"[复原] 《{title}》已重新加入队列",
                f"[復元] 「{title}」をキューに戻しました",
            )
        )
        signal_bus.task_status_changed.emit(task_id, TaskStatus.QUEUED_META.value)
        self._schedule_task_persist()
        self._try_activate()
        return True

    def restore_all_cancelled(self) -> int:
        """Put all cancelled tasks back into the queue."""
        return self._restore_cancelled_tasks(origin_filter="")

    def _restore_auto_stalled_cancelled_if_idle(self) -> int:
        if not app_config.auto_restore_stalled_cancelled:
            return 0
        return self._restore_cancelled_tasks(origin_filter=_CANCEL_ORIGIN_AUTO_STALL, only_when_idle=True)

    def _restore_cancelled_tasks(self, *, origin_filter: str = "", only_when_idle: bool = False) -> int:
        """Put cancelled tasks back into the queue, optionally filtered by origin."""
        restored_ids: list[str] = []
        with self._lock:
            if only_when_idle and any(
                task.status not in _TERMINAL_STATUSES
                for task in self._tasks.values()
            ):
                return 0
            for task in list(self._tasks.values()):
                if task.status != TaskStatus.CANCELLED:
                    continue
                if origin_filter and task.cancel_origin != origin_filter:
                    continue
                self._restore_cancelled_task_locked(task)
                restored_ids.append(task.task_id)

        for task_id in restored_ids:
            signal_bus.task_status_changed.emit(task_id, TaskStatus.QUEUED_META.value)
        if restored_ids:
            signal_bus.log_message.emit(
                tr(
                    f"[Restore] re-queued {len(restored_ids)} cancelled tasks",
                    f"[复原] 已重新加入队列 {len(restored_ids)} 个中断任务",
                    f"[復元] {len(restored_ids)} 件の中断タスクをキューへ戻しました",
                )
            )
            self._schedule_task_persist()
            self._try_activate()
        return len(restored_ids)

    def retry_all_failed(self, exclude_downloaded: bool = True) -> tuple[int, int]:
        """Retry all failed tasks.

        Args:
            exclude_downloaded: if True, failed tasks that already have local
                completed files are marked completed instead of retried.

        Returns:
            (retried_count, skipped_as_completed_count)
        """
        to_retry: list[tuple[str, str, str]] = []
        to_complete: list[str] = []

        with self._lock:
            failed_tasks = [
                t for t in self._tasks.values() if t.status == TaskStatus.FAILED
            ]

        for task in failed_tasks:
            if exclude_downloaded and self._find_existing_local_file(task.video_id):
                to_complete.append(task.task_id)
                continue

            to_retry.append(
                (task.task_id, task.file_path, task.title or task.video_id)
            )

        retried_count = 0
        for tid in to_complete:
            self._complete_task(tid)
        for tid, file_path, title in to_retry:
            _, cleanup_failed = self._log_retry_temp_cleanup(title, file_path)
            if cleanup_failed:
                signal_bus.task_error.emit(
                    tid,
                    tr(
                        "Temp cache cleanup failed; retry was not started",
                        "临时缓存清理失败，未开始重试",
                        "一時キャッシュ削除に失敗したため再試行しません",
                    ),
                )
                continue
            with self._lock:
                task = self._tasks.get(tid)
                if not task or task.status != TaskStatus.FAILED:
                    continue
                task.status = TaskStatus.QUEUED_META
                task.cancel_requested = False
                task.delete_temp_on_cancel = False
                task.remove_after_cancel = False
                task.cancel_origin = ""
                task.aria2_gid = ""
                task.error_msg = ""
                task.downloaded_bytes = 0
                task.total_bytes = 0
                task.download_url = ""
                self._unmark_terminal_locked(tid)
                self._queued_meta_ids.append(tid)
            retried_count += 1
            signal_bus.task_status_changed.emit(tid, TaskStatus.QUEUED_META.value)

        if retried_count or to_complete:
            self._schedule_task_persist()
        self._try_activate()
        return retried_count, len(to_complete)

    def cancel_task(
        self,
        task_id: str,
        *,
        delete_temp: bool = False,
        remove_after_cancel: bool = False,
    ) -> bool:
        """Request cancellation for a queued or active task."""
        finalize_now = False
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return False
            if task.status in _TERMINAL_STATUSES:
                return False
            task.cancel_requested = True
            task.cancel_origin = _CANCEL_ORIGIN_MANUAL
            task.delete_temp_on_cancel = task.delete_temp_on_cancel or delete_temp
            task.remove_after_cancel = task.remove_after_cancel or remove_after_cancel
            if task.status in (TaskStatus.QUEUED_META, TaskStatus.QUEUED_DOWNLOAD):
                finalize_now = True
            else:
                task.status = TaskStatus.CANCELLING
            self._task_last_activity.pop(task_id, None)

        if finalize_now:
            self._cancel_task_terminal(
                task_id,
                tr("Cancelled by user", "用户已中断", "ユーザーが中断しました"),
            )
        else:
            signal_bus.task_status_changed.emit(task_id, TaskStatus.CANCELLING.value)
            self._schedule_task_persist()
        return True

    def cancel_all_active(self) -> int:
        with self._lock:
            ids = [
                tid
                for tid, task in self._tasks.items()
                if task.status not in _TERMINAL_STATUSES
            ]
        for task_id in ids:
            self.cancel_task(task_id)
        return len(ids)

    def remove_task(self, task_id: str):
        should_cancel = False
        with self._lock:
            task = self._tasks.get(task_id)
            if task and task.status not in _TERMINAL_STATUSES:
                should_cancel = True
                removed = None
            else:
                removed = self._forget_task_locked(task_id)
        if should_cancel:
            self.cancel_task(task_id, remove_after_cancel=True)
            return
        if removed:
            signal_bus.task_removed.emit(task_id)
            self._schedule_task_persist()

    def clear_completed(self):
        """Remove finished tasks from UI board, keeping retry/restore candidates."""
        removed_ids: list[str] = []
        with self._lock:
            to_remove = [
                tid
                for tid, t in self._tasks.items()
                if t.status in (TaskStatus.COMPLETED, TaskStatus.SKIPPED)
            ]
            for tid in to_remove:
                if self._forget_task_locked(tid):
                    removed_ids.append(tid)
        for tid in removed_ids:
            signal_bus.task_removed.emit(tid)
        if removed_ids:
            self._schedule_task_persist()
