"""Finish tasks (completed, failed, cancelled), keep the terminal index and prune old tasks."""

from __future__ import annotations

import gc
import time

from ...i18n import tr
from ...logging_setup import get_logger
from ...signal_bus import signal_bus
from ..manager_state import (
    CANCEL_ORIGIN_MANUAL as _CANCEL_ORIGIN_MANUAL,
    PRUNABLE_TERMINAL_STATUSES as _PRUNABLE_TERMINAL_STATUSES,
    TERMINAL_STATUSES as _TERMINAL_STATUSES,
)
from ..models import DownloadTask, TaskStatus


logger = get_logger(__name__)


class TerminalStatesMixin:
    """Finish tasks (completed, failed, cancelled), keep the terminal index and prune old tasks."""

    def _forget_task_locked(self, task_id: str) -> DownloadTask | None:
        """Remove one live task and all side indexes. Must hold self._lock."""
        task = self._tasks.pop(task_id, None)
        if not task:
            return None
        self._task_id_by_video_id.pop(task.video_id.lower(), None)
        self._active_task_ids.discard(task_id)
        self._terminal_task_id_set.discard(task_id)
        self._task_last_activity.pop(task_id, None)
        return task

    def _unmark_terminal_locked(self, task_id: str):
        """Mark a previously-terminal task as live again. Must hold self._lock."""
        self._terminal_task_id_set.discard(task_id)
        self._task_last_activity.pop(task_id, None)

    def _mark_terminal_locked(self, task: DownloadTask):
        """Track terminal tasks for bounded live-memory retention."""
        self._active_task_ids.discard(task.task_id)
        self._task_last_activity.pop(task.task_id, None)
        task.download_url = ""
        task.file_url = ""
        task.raw_json = ""
        task.tags_json = ""
        task.thumbnail_url = ""
        task.speed_str = ""
        task.aria2_gid = ""
        task.file_id = ""
        self._terminal_events_since_gc += 1
        if task.task_id not in self._terminal_task_id_set:
            self._terminal_task_ids.append(task.task_id)
            self._terminal_task_id_set.add(task.task_id)

    def _prune_terminal_tasks(self) -> list[str]:
        removed: list[str] = []
        with self._lock:
            prunable_count = sum(
                1
                for task_id in self._terminal_task_id_set
                if (
                    (task := self._tasks.get(task_id))
                    and task.status in _PRUNABLE_TERMINAL_STATUSES
                )
            )
            while self._terminal_task_ids and prunable_count > self._terminal_keep_limit:
                task_id = self._terminal_task_ids.popleft()
                if task_id not in self._terminal_task_id_set:
                    continue
                task = self._tasks.get(task_id)
                if not task or task.status not in _TERMINAL_STATUSES:
                    self._terminal_task_id_set.discard(task_id)
                    continue
                if task.status not in _PRUNABLE_TERMINAL_STATUSES:
                    continue
                if self._forget_task_locked(task_id):
                    removed.append(task_id)
                    prunable_count -= 1
        if removed:
            signal_bus.tasks_removed.emit(removed)
        self._maybe_collect_garbage()
        return removed

    def _maybe_collect_garbage(self):
        should_collect = False
        now = time.monotonic()
        with self._lock:
            if self._terminal_events_since_gc >= 20 and now - self._last_gc_at >= 15:
                self._terminal_events_since_gc = 0
                self._last_gc_at = now
                should_collect = True
        if should_collect:
            gc.collect()

    def _restore_cancelled_task_locked(self, task: DownloadTask):
        """Reset a cancelled task for a fresh resolve. Must hold self._lock."""
        task.status = TaskStatus.QUEUED_META
        task.cancel_requested = False
        task.delete_temp_on_cancel = False
        task.remove_after_cancel = False
        task.cancel_origin = ""
        task.aria2_gid = ""
        task.error_msg = ""
        task.speed_str = ""
        task.downloaded_bytes = 0
        task.total_bytes = 0
        task.download_url = ""
        self._unmark_terminal_locked(task.task_id)
        self._queued_meta_ids.append(task.task_id)

    def _cancel_task_terminal(self, task_id: str, reason: str, *, origin: str = ""):
        remove_after = False
        delete_temp = False
        removed_temp = failed_temp = 0
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            if task.status in _TERMINAL_STATUSES:
                return
            task.status = TaskStatus.CANCELLED
            task.error_msg = reason
            if origin:
                task.cancel_origin = origin
            elif not task.cancel_origin:
                task.cancel_origin = _CANCEL_ORIGIN_MANUAL
            task.speed_str = ""
            task.aria2_gid = ""
            remove_after = task.remove_after_cancel
            delete_temp = task.delete_temp_on_cancel
            if not remove_after:
                self._mark_terminal_locked(task)
        if delete_temp:
            removed_temp, failed_temp = self._remove_task_temp_files(task)
            signal_bus.log_message.emit(
                tr(
                    f"[Cancelled] cleaned temp files for \"{task.title or task.video_id}\": removed {removed_temp}, failed {failed_temp}",
                    f"[已中断] 已清理《{task.title or task.video_id}》临时文件: 删除 {removed_temp} 个，失败 {failed_temp} 个",
                    f"[中断] 「{task.title or task.video_id}」一時ファイル削除: {removed_temp} / 失敗 {failed_temp}",
                )
            )

        signal_bus.task_status_changed.emit(task_id, TaskStatus.CANCELLED.value)
        signal_bus.log_message.emit(
            tr(
                f"[Cancelled] \"{task.title or task.video_id}\"",
                f"[已中断] 《{task.title or task.video_id}》",
                f"[中断] 「{task.title or task.video_id}」",
            )
        )
        if remove_after:
            with self._lock:
                self._forget_task_locked(task_id)
            signal_bus.task_removed.emit(task_id)
        else:
            self._prune_terminal_tasks()
        self._schedule_task_persist()
        self._try_activate()

    def _fail_task(self, task_id: str, reason: str):
        cancel_requested = False
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            cancel_requested = task.cancel_requested
            if cancel_requested:
                task.status = TaskStatus.CANCELLING
            else:
                task.status = TaskStatus.FAILED
                task.error_msg = reason
                self._mark_terminal_locked(task)
        if cancel_requested:
            self._cancel_task_terminal(
                task_id,
                tr("Cancelled by user", "用户已中断", "ユーザーが中断しました"),
            )
            return
        signal_bus.task_status_changed.emit(task_id, TaskStatus.FAILED.value)
        signal_bus.task_error.emit(task_id, reason)
        self._prune_terminal_tasks()
        self._schedule_task_persist()
        # Free concurrency slot
        self._try_activate()

    def _complete_task(self, task_id: str):
        task = self._tasks.get(task_id)
        if not task:
            return
        if self._is_cancel_requested(task_id):
            self._cancel_task_terminal(
                task_id,
                tr("Cancelled by user", "用户已中断", "ユーザーが中断しました"),
            )
            return
        with self._lock:
            task.status = TaskStatus.COMPLETED
        rule_payload = self._task_rule_payload(task)
        if rule_payload["download_thumbnail"]:
            self._download_thumbnail(task)
        if rule_payload["collect_nfo_info"]:
            self._write_nfo(task)
        if rule_payload["record_to_history"]:
            try:
                self.history.upsert_downloaded(
                    {
                        "video_id": task.video_id,
                        "title": task.title,
                        "author": task.author,
                        "published_at": task.published_at,
                        "likes": task.likes,
                        "views": task.views,
                        "slug": task.slug,
                        "rating": task.rating,
                        "duration": task.duration,
                        "comments": task.comments,
                        "tags_json": task.tags_json,
                        "raw_json": task.raw_json,
                        "source_url": task.url,
                        "file_path": task.file_path,
                        "thumbnail_path": task.thumbnail_path,
                        "quality": task.quality,
                    }
                )
            except Exception as exc:
                logger.exception("Failed to write history DB for task %s", task_id)
                signal_bus.log_message.emit(
                    tr(
                        f"[Warning] Failed to write history DB (download file is safe): {exc}",
                        f"[警告] 写入历史库失败（不影响文件下载）: {exc}",
                        f"[警告] 履歴DB書き込み失敗（ダウンロードファイルには影響なし）: {exc}",
                    )
                )
        else:
            signal_bus.log_message.emit(
                tr(
                    f"[History] skipped for \"{task.title}\" by rule choice",
                    f"[历史] 按规则选择跳过《{task.title}》的历史记录",
                    f"[履歴] ルール設定により「{task.title}」を履歴へ記録しません",
                )
            )
        with self._lock:
            live_task = self._tasks.get(task_id)
            if live_task:
                if rule_payload["record_to_history"]:  # a temporary copy is not "already downloaded"
                    with self._existing_file_index_lock:
                        self._existing_file_index[live_task.video_id.lower()] = live_task.file_path
                self._mark_terminal_locked(live_task)
        signal_bus.task_status_changed.emit(task_id, TaskStatus.COMPLETED.value)
        self._prune_terminal_tasks()
        self._schedule_task_persist()
        # Free concurrency slot
        self._try_activate()
