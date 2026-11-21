"""User commands on queued tasks: priority, retry, restore, cancel, remove, clear."""

from __future__ import annotations

import time

from ...config import app_config
from ...i18n import tr
from ...signal_bus import signal_bus
from .manager_state import (
    CANCEL_ORIGIN_AUTO_STALL as _CANCEL_ORIGIN_AUTO_STALL,
    CANCEL_ORIGIN_MANUAL as _CANCEL_ORIGIN_MANUAL,
    STALL_AUTO_RESTORE_LIMIT as _STALL_AUTO_RESTORE_LIMIT,
    TERMINAL_STATUSES as _TERMINAL_STATUSES,
)
from ..models import DownloadTask, TaskStatus
from .retry_policy import should_auto_retry


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
            if task and task.status == TaskStatus.FAILED and task_id not in self._manual_retry_claims:
                # Claimed until re-queued, so automatic retry cannot pick the task
                # up while its temp files are being removed.
                self._manual_retry_claims.add(task_id)
                temp_file_path = task.file_path
                temp_title = task.title or task.video_id or task_id
        if not temp_title:
            return
        try:
            self._retry_claimed_task(task_id, temp_title, temp_file_path)
        finally:
            with self._lock:
                self._manual_retry_claims.discard(task_id)

    def _retry_claimed_task(self, task_id: str, temp_title: str, temp_file_path: str):
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
                task.auto_retry_count = 0
                task.auto_restore_count = 0
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
            if self._task_worker_alive_locked(task_id):
                stopping_title = task.title or task.video_id
            else:
                stopping_title = ""
        if stopping_title:
            # its previous job is still winding down; a second one would share the temp file
            signal_bus.log_message.emit(
                tr(
                    f"[Restore] \"{stopping_title}\" is still stopping; try again in a moment",
                    f"[复原] 《{stopping_title}》仍在停止中，请稍后再试",
                    f"[復元] 「{stopping_title}」はまだ停止処理中です。少し待ってから再試行してください",
                )
            )
            return False
        with self._lock:
            task = self._tasks.get(task_id)
            if not task or task.status != TaskStatus.CANCELLED or self._task_worker_alive_locked(task_id):
                return False
            title = task.title or task.video_id
            self._restore_cancelled_task_locked(task)
            task.auto_restore_count = 0
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
        return self._auto_requeue_if_idle(retry_failed=False)[0]

    def _auto_retry_failed_if_idle(self) -> int:
        return self._auto_requeue_if_idle(restore_stalled=False)[1]

    def _queue_idle_locked(self) -> bool:
        """No task is queued or running.  Tasks waiting on the workbench do not count."""
        return all(
            task.status in _TERMINAL_STATUSES or task.status == TaskStatus.STAGED
            for task in self._tasks.values()
        )

    def _auto_requeue_candidate_locked(self, task: DownloadTask) -> bool:
        """The task's last job has exited and no manual retry is working on it."""
        return (
            not self._task_worker_alive_locked(task.task_id)
            and task.task_id not in self._manual_retry_claims
            and task.task_id not in self._orphan_aria2_gids
        )

    def _auto_requeue_if_idle(self, *, restore_stalled: bool = True, retry_failed: bool = True) -> tuple[int, int]:
        """Once nothing is queued or running, re-queue in one pass:

        * tasks the idle watchdog cancelled (``auto_restore_stalled_cancelled``),
          at most ``STALL_AUTO_RESTORE_LIMIT`` times each;
        * failed tasks whose error looks transient (``auto_retry_failed_enabled``),
          at most ``auto_retry_failed_max_attempts`` times each.

        Both keep the partial file so the download resumes.  Counters reset when
        the user retries or restores the task by hand.  Nothing happens before
        :meth:`start`, while the startup sign-in is still running, or during
        shutdown.
        """
        restore_stalled = restore_stalled and bool(app_config.auto_restore_stalled_cancelled)
        retry_failed = retry_failed and bool(app_config.auto_retry_failed_enabled)
        if not (restore_stalled or retry_failed):
            return 0, 0
        self._settle_orphan_aria2_gids(time.monotonic())
        max_attempts = app_config.auto_retry_failed_max_attempts
        retry_keywords = app_config.auto_retry_failed_retry_keywords
        skip_keywords = app_config.auto_retry_failed_skip_keywords
        restored: list[str] = []
        retried: list[tuple[str, str, int]] = []
        with self._lock:
            if (
                not self._started
                or self._shutting_down
                or self._login_pending
                or not self._queue_idle_locked()
            ):
                return 0, 0
            for task in list(self._tasks.values()):
                if not self._auto_requeue_candidate_locked(task):
                    continue
                if (
                    restore_stalled
                    and task.status == TaskStatus.CANCELLED
                    and task.cancel_origin == _CANCEL_ORIGIN_AUTO_STALL
                    and task.auto_restore_count < _STALL_AUTO_RESTORE_LIMIT
                ):
                    count = task.auto_restore_count + 1
                    self._restore_cancelled_task_locked(task)
                    task.auto_restore_count = count
                    restored.append(task.task_id)
                elif (
                    retry_failed
                    and task.status == TaskStatus.FAILED
                    and task.auto_retry_count < max_attempts
                    and should_auto_retry(task.error_msg, retry_keywords=retry_keywords, skip_keywords=skip_keywords)
                ):
                    attempt = task.auto_retry_count + 1
                    self._restore_cancelled_task_locked(task)
                    task.auto_retry_count = attempt
                    retried.append((task.task_id, task.title or task.video_id, attempt))

        if restored:
            signal_bus.log_message.emit(
                tr(
                    f"[Restore] re-queued {len(restored)} cancelled tasks",
                    f"[复原] 已重新加入队列 {len(restored)} 个中断任务",
                    f"[復元] {len(restored)} 件の中断タスクをキューへ戻しました",
                )
            )
        for task_id, title, attempt in retried:
            signal_bus.log_message.emit(
                tr(
                    f"[Auto-retry] \"{title}\" re-queued ({attempt}/{max_attempts})",
                    f"[自动重试] 《{title}》已重新加入队列（{attempt}/{max_attempts}）",
                    f"[自動再試行] 「{title}」をキューに戻しました（{attempt}/{max_attempts}）",
                )
            )
        for task_id in [*restored, *(item[0] for item in retried)]:
            signal_bus.task_status_changed.emit(task_id, TaskStatus.QUEUED_META.value)
        if restored or retried:
            self._schedule_task_persist()
            self._try_activate()
        return len(restored), len(retried)

    def _restore_cancelled_tasks(self, *, origin_filter: str = "") -> int:
        """Put cancelled tasks back into the queue, optionally filtered by origin."""
        restored_ids: list[str] = []
        with self._lock:
            for task in list(self._tasks.values()):
                if task.status != TaskStatus.CANCELLED:
                    continue
                if origin_filter and task.cancel_origin != origin_filter:
                    continue
                if self._task_worker_alive_locked(task.task_id):
                    continue  # still stopping; restored on a later attempt
                self._restore_cancelled_task_locked(task)
                task.auto_restore_count = 0
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
                t
                for t in self._tasks.values()
                if t.status == TaskStatus.FAILED and t.task_id not in self._manual_retry_claims
            ]
            claimed = {t.task_id for t in failed_tasks}
            self._manual_retry_claims.update(claimed)
        try:
            return self._retry_claimed_failed(failed_tasks, exclude_downloaded)
        finally:
            with self._lock:
                self._manual_retry_claims.difference_update(claimed)

    def _retry_claimed_failed(self, failed_tasks: list[DownloadTask], exclude_downloaded: bool) -> tuple[int, int]:
        to_retry: list[tuple[str, str, str]] = []
        to_complete: list[str] = []

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
                task.auto_retry_count = 0
                task.auto_restore_count = 0
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
            if task and task.status not in _TERMINAL_STATUSES and task.status != TaskStatus.STAGED:
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
