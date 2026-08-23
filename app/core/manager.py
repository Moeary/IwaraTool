"""Download manager: state-machine scheduler + thread pool executor.

State machine flow
──────────────────
QUEUED_META → RESOLVING → QUEUED_DOWNLOAD → DOWNLOADING → COMPLETED
                                                        ↘ FAILED
                        RESOLVING ─────────────────────→ SKIPPED (filtered)

Concurrency rule
────────────────
At any moment:  len(RESOLVING) + len(QUEUED_DOWNLOAD) + len(DOWNLOADING) ≤ max_concurrent

This ensures download URLs are never resolved too early and expire before use.
"""

from __future__ import annotations

import os
import json
import shutil
import subprocess
import threading
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlparse

from ..config import app_config
from ..i18n import tr
from ..signal_bus import signal_bus
from .api import IwaraAPI
from .download_paths import DownloadPathMixin
from .download_runtime import DownloadRuntimeMixin
from .repair_manager import RepairManagerMixin
from .rules import active_rule_id, current_rule_payload, normalize_rule_payload, rule_store
from .search_manager import SearchManagerMixin
from .subscription_manager import SubscriptionManagerMixin
from .download_policy import SharedRateLimiter
from .history import DownloadHistory
from .image_cache import SearchImageCache, SubscriptionImageCache
from .manager_state import (
    CANCEL_ORIGIN_AUTO_STALL as _CANCEL_ORIGIN_AUTO_STALL,
    CANCEL_ORIGIN_MANUAL as _CANCEL_ORIGIN_MANUAL,
    CANCEL_ORIGIN_SHUTDOWN as _CANCEL_ORIGIN_SHUTDOWN,
    LIVE_TERMINAL_KEEP_LIMIT as _LIVE_TERMINAL_KEEP_LIMIT,
    TERMINAL_STATUSES as _TERMINAL_STATUSES,
)
from .models import DownloadTask, TaskStatus
from .subscriptions import SubscriptionStore
from .tag_dictionary import TagDictionary
from .task_metadata import (
    _compact_video_raw_json,
    _iwara_image_url,
    _subscription_item_from_video,
)
from .task_queue import TaskPersistenceCoordinator, TaskQueueStore

if TYPE_CHECKING:
    pass


class DownloadManager(DownloadRuntimeMixin, SubscriptionManagerMixin, SearchManagerMixin, RepairManagerMixin, DownloadPathMixin):
    """Central manager for all download tasks.

    Thread-safe: all internal state mutations are protected by self._lock.
    Qt signals are emitted *outside* the lock to avoid deadlocks.
    """

    def __init__(self, *, task_store: TaskQueueStore | None = None):
        self.api = IwaraAPI()
        self.history = DownloadHistory()
        self.subscriptions = SubscriptionStore()
        self.search_image_cache = SearchImageCache()
        self.subscription_image_cache = SubscriptionImageCache()
        self.tag_dictionary = TagDictionary()
        self._migrate_subscription_avatar_cache()

        # task_id → DownloadTask
        self._tasks: dict[str, DownloadTask] = {}
        self._task_id_by_video_id: dict[str, str] = {}
        self._queued_meta_ids: deque[str] = deque()
        self._active_task_ids: set[str] = set()
        self._terminal_task_ids: deque[str] = deque()
        self._terminal_task_id_set: set[str] = set()
        self._lock = threading.Lock()
        self._api_lock = threading.RLock()
        self._subscription_refresh_guard = threading.Lock()
        self._existing_file_index_lock = threading.Lock()
        self._existing_file_index: dict[str, str] = {}
        self._existing_file_index_root = ""
        self._existing_file_index_built_at = 0.0
        self._terminal_keep_limit = _LIVE_TERMINAL_KEEP_LIMIT
        self._terminal_events_since_gc = 0
        self._last_gc_at = 0.0
        self._task_last_activity: dict[str, float] = {}
        self._task_store = task_store
        self._task_persistence: TaskPersistenceCoordinator | None = None
        self._shutting_down = False
        self._started = False
        self._last_aria2_global_limit = ""
        self._last_aria2_limit_attempted = ""
        self._last_aria2_limit_attempt_at = 0.0
        self._rate_limiter = SharedRateLimiter(self._global_speed_limit_bytes)

        # Keep parse, resolve, and download work isolated so a large batch cannot
        # starve metadata resolution or leave the UI looking stuck.
        self._parse_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="iwara-parse")
        self._resolve_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="iwara-resolve")
        self._download_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="iwara-download")
        self._restored_task_count = self._load_persisted_tasks()
        if self._task_store is not None:
            self._task_persistence = TaskPersistenceCoordinator(
                self._task_store,
                self._create_task_snapshot,
            )
        self._watchdog_stop = threading.Event()
        self._watchdog_thread = threading.Thread(
            target=self._stall_watchdog_loop,
            name="iwara-stall-watchdog",
            daemon=True,
        )
        self._watchdog_thread.start()

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self):
        """Activate tasks restored from disk after configuration and login load."""
        with self._lock:
            if self._started or self._shutting_down:
                return
            self._started = True
            restored_count = self._restored_task_count
        if self._task_store is not None and self._task_store.last_error:
            signal_bus.log_message.emit(
                tr(
                    f"[Queue] Saved task queue could not be read: {self._task_store.last_error}",
                    f"[队列] 无法读取已保存任务：{self._task_store.last_error}",
                    f"[キュー] 保存済みタスクを読み込めませんでした: {self._task_store.last_error}",
                )
            )
        elif restored_count:
            signal_bus.log_message.emit(
                tr(
                    f"[Queue] restored {restored_count} tasks from the previous session",
                    f"[队列] 已恢复上次会话的 {restored_count} 个任务",
                    f"[キュー] 前回のセッションから {restored_count} 件を復元しました",
                )
            )
        self._try_activate()

    def pending_task_count(self) -> int:
        """Return the number of queued or running tasks that need safe shutdown."""
        with self._lock:
            return sum(
                1
                for task in self._tasks.values()
                if task.status not in _TERMINAL_STATUSES
            )

    def flush_task_queue(self):
        """Synchronously persist the current recoverable queue state."""
        if self._task_persistence is not None:
            self._task_persistence.flush()

    def shutdown(self, *, wait: bool = False) -> int:
        """Pause active work, persist it for restart, and stop background workers."""
        with self._lock:
            if self._shutting_down:
                return 0
            self._shutting_down = True
            pending = 0
            for task in self._tasks.values():
                if task.status in _TERMINAL_STATUSES:
                    continue
                pending += 1
                if task.status != TaskStatus.QUEUED_META:
                    task.cancel_requested = True
                    task.cancel_origin = _CANCEL_ORIGIN_SHUTDOWN
                    task.delete_temp_on_cancel = False
                    task.remove_after_cancel = False

        # Close persistence before workers observe cancellation so the saved
        # snapshot represents paused work that will be re-queued on next start.
        if self._task_persistence is not None:
            self._task_persistence.close()
        self._watchdog_stop.set()
        self._parse_executor.shutdown(wait=wait, cancel_futures=True)
        self._resolve_executor.shutdown(wait=wait, cancel_futures=True)
        self._download_executor.shutdown(wait=wait, cancel_futures=True)
        if wait and self._watchdog_thread is not threading.current_thread():
            self._watchdog_thread.join(timeout=2)
        return pending

    def _load_persisted_tasks(self) -> int:
        if self._task_store is None:
            return 0
        restored = self._task_store.load()
        with self._lock:
            for task in restored:
                video_key = task.video_id.casefold()
                if task.task_id in self._tasks or video_key in self._task_id_by_video_id:
                    continue
                self._tasks[task.task_id] = task
                self._task_id_by_video_id[video_key] = task.task_id
                if task.status == TaskStatus.QUEUED_META:
                    self._queued_meta_ids.append(task.task_id)
                elif task.status in _TERMINAL_STATUSES:
                    self._terminal_task_ids.append(task.task_id)
                    self._terminal_task_id_set.add(task.task_id)
        return len(restored)

    def _create_task_snapshot(self) -> dict[str, Any]:
        if self._task_store is None:
            return {"version": 1, "tasks": []}
        with self._lock:
            return self._task_store.create_snapshot(self._tasks.values())

    def _schedule_task_persist(self):
        if self._task_persistence is not None:
            self._task_persistence.schedule()

    def get_tasks(self) -> list[DownloadTask]:
        with self._lock:
            return list(self._tasks.values())

    def get_task(self, task_id: str) -> DownloadTask | None:
        with self._lock:
            return self._tasks.get(task_id)

    def add_url(self, url: str, *, rule_id: str = ""):
        """Parse URL and enqueue tasks (runs in background thread)."""
        self._parse_executor.submit(self._parse_and_enqueue, url, str(rule_id or ""))

    def add_url_mark_downloaded(self, url: str, *, rule_id: str = ""):
        """Parse URL and mark resolved videos as already downloaded in history."""
        self._parse_executor.submit(self._parse_and_mark_downloaded, url, str(rule_id or ""))

    def enqueue_video_ids(
        self,
        video_ids: list[str],
        *,
        source_label: str = "",
        priority: int = 0,
        rule_id: str = "",
    ) -> int:
        """Queue a list of video ids and return how many were accepted for parsing."""
        items = [
            (str(raw_id or "").strip(), f"https://www.iwara.tv/video/{str(raw_id or '').strip()}")
            for raw_id in video_ids
            if str(raw_id or "").strip()
        ]
        summary = self._enqueue_video_ids_bulk(
            items,
            source_label=source_label,
            priority=priority,
            rule_id=rule_id,
        )
        return int(summary.get("queued", 0) or 0)

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

    def get_history_records(self) -> list[dict[str, Any]]:
        return self.history.list_records()

    def sync_history_with_download_folder(self) -> dict[str, int]:
        return self.history.sync_with_download_folder(app_config.download_dir)

    def remove_history_record(self, video_id: str):
        self.history.remove(video_id)

    def remove_history_records(self, video_ids: list[str]) -> int:
        return self.history.remove_many(video_ids)

    # ── Subscriptions ────────────────────────────────────────────────────────

    def open_history_output(
        self, video_id: str, *, open_file: bool = False
    ) -> tuple[bool, str]:
        record = self.history.get_record(video_id)
        if not record:
            return False, tr("History record does not exist", "历史记录不存在", "履歴が存在しません")

        file_path = str(record.get("file_path", "") or "")
        if not file_path or not os.path.exists(file_path):
            return False, tr("File does not exist", "文件不存在", "ファイルが存在しません")

        target = file_path if open_file else os.path.dirname(file_path)
        return self._open_system_path(target)

    def rename_history_file(
        self, video_id: str, new_filename: str
    ) -> tuple[bool, str]:
        record = self.history.get_record(video_id)
        if not record:
            return False, tr("History record does not exist", "历史记录不存在", "履歴が存在しません")

        old_path = str(record.get("file_path", "") or "")
        if not old_path or not os.path.isfile(old_path):
            return False, tr("File does not exist", "文件不存在", "ファイルが存在しません")

        old_dir = os.path.dirname(old_path)
        old_ext = os.path.splitext(old_path)[1] or ".mp4"
        cleaned_name = self._sanitize_path_segment(new_filename.strip())
        if cleaned_name in ("", "_"):
            return False, tr("Invalid file name", "文件名无效", "ファイル名が不正です")
        if not os.path.splitext(cleaned_name)[1]:
            cleaned_name += old_ext

        new_path = os.path.join(old_dir, cleaned_name)
        if os.path.abspath(new_path) == os.path.abspath(old_path):
            return False, tr("File name is unchanged", "文件名没有变化", "ファイル名は変更されていません")
        if os.path.exists(new_path):
            return False, tr("Target file already exists", "目标文件已存在", "変更先ファイルは既に存在します")

        old_stem = os.path.splitext(old_path)[0]
        new_stem = os.path.splitext(new_path)[0]
        sidecars: list[tuple[str, str]] = []

        old_thumbnail = str(record.get("thumbnail_path", "") or "")
        if old_thumbnail and os.path.isfile(old_thumbnail):
            thumb_ext = os.path.splitext(old_thumbnail)[1] or ".jpg"
            sidecars.append((old_thumbnail, new_stem + thumb_ext))

        nfo_path = old_stem + ".nfo"
        if os.path.isfile(nfo_path):
            sidecars.append((nfo_path, new_stem + ".nfo"))

        for _, target in sidecars:
            if os.path.exists(target):
                return False, tr(
                    f"Sidecar target already exists: {target}",
                    f"同名附属文件已存在: {target}",
                    f"関連ファイルの変更先が既に存在します: {target}",
                )

        new_thumbnail = old_thumbnail
        try:
            os.rename(old_path, new_path)
            for source, target in sidecars:
                os.rename(source, target)
                if source == old_thumbnail:
                    new_thumbnail = target
        except Exception as exc:
            return False, str(exc)

        self.history.update_file_paths(
            video_id,
            file_path=new_path,
            thumbnail_path=new_thumbnail if os.path.exists(new_thumbnail) else "",
        )
        signal_bus.log_message.emit(
            tr(
                f"[History] Renamed file: {old_path} -> {new_path}",
                f"[历史] 已重命名文件: {old_path} -> {new_path}",
                f"[履歴] ファイル名を変更しました: {old_path} -> {new_path}",
            )
        )
        return True, new_path

    def open_task_output(
        self, task_id: str, *, open_file: bool | None = None
    ) -> tuple[bool, str]:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return False, tr("Task does not exist", "任务不存在", "タスクが存在しません")
            status = task.status
            file_path = task.file_path
            title = task.title or task.video_id

        if status != TaskStatus.COMPLETED:
            return False, tr(
                "Only completed tasks can be opened",
                "仅支持已完成任务",
                "完了タスクのみ開けます",
            )
        if not file_path or not os.path.exists(file_path):
            return False, tr("File does not exist", "文件不存在", "ファイルが存在しません")

        action = ""
        if open_file is None:
            action = str(app_config.completed_task_click_action or "folder").lower()
            open_file = action == "player"
        target = file_path if open_file else os.path.dirname(file_path)
        if not target:
            return False, tr("No openable path", "无可打开路径", "開けるパスがありません")

        try:
            if os.name == "nt":
                os.startfile(target)
            elif shutil.which("xdg-open"):
                subprocess.Popen(["xdg-open", target])
            elif shutil.which("open"):
                subprocess.Popen(["open", target])
            else:
                return False, tr(
                    "System does not support auto-open",
                    "系统不支持自动打开",
                    "システムが自動オープンに対応していません",
                )
        except Exception as exc:
            return False, str(exc)

        if action == "player":
            action_text = tr("Player", "播放器", "プレイヤー")
        else:
            action_text = tr("File", "文件", "ファイル") if open_file else tr("Folder", "文件夹", "フォルダー")
        signal_bus.log_message.emit(
            tr(
                f"[Open] \"{title}\" -> {action_text}",
                f"[打开] 《{title}》 → {action_text}",
                f"[開く] 「{title}」 -> {action_text}",
            )
        )
        return True, ""

    def _open_system_path(self, target: str) -> tuple[bool, str]:
        if not target:
            return False, tr("No openable path", "无可打开路径", "開けるパスがありません")
        try:
            if os.name == "nt":
                os.startfile(target)
            elif shutil.which("xdg-open"):
                subprocess.Popen(["xdg-open", target])
            elif shutil.which("open"):
                subprocess.Popen(["open", target])
            else:
                return False, tr(
                    "System does not support auto-open",
                    "系统不支持自动打开",
                    "システムが自動オープンに対応していません",
                )
        except Exception as exc:
            return False, str(exc)
        return True, ""

    def set_login(self, logged_in: bool, token: str | None = None):
        with self._api_lock:
            if logged_in and token:
                self.api.token = token
                app_config.auth_token = token
                app_config.auth_token_saved_at = datetime.now().isoformat(timespec="seconds")
            elif not logged_in:
                self.api.token = None
                app_config.auth_token = ""
                app_config.auth_token_saved_at = ""

    def restore_cached_login(self) -> bool:
        token = (app_config.auth_token or "").strip()
        if not (app_config.auth_enabled and token):
            return False
        with self._api_lock:
            self.api.token = token
        return True

    def apply_config(self):
        """Apply proxy settings from app_config to the scraper."""
        with self._api_lock:
            if app_config.api_proxy_enabled and app_config.api_proxy_url:
                self.api.set_proxy(app_config.api_proxy_url)
            else:
                self.api.set_proxy("")

    def _download_request_proxies(self) -> dict[str, str | None]:
        proxy_url = (app_config.download_proxy_url or "").strip()
        if app_config.download_proxy_enabled and proxy_url:
            return {"http": proxy_url, "https": proxy_url}
        return {"http": None, "https": None}

    def _api_call(self, method_name: str, *args, **kwargs):
        """Serialize access to the shared cloudscraper session."""
        with self._api_lock:
            method = getattr(self.api, method_name)
            return method(*args, **kwargs)

    def _current_token(self) -> str:
        with self._api_lock:
            return self.api.token or ""

    # ── URL parsing ───────────────────────────────────────────────────────────

    def _parse_and_enqueue(self, raw: str, rule_id: str = ""):
        url = raw.strip()
        if not url:
            return
        signal_bus.log_message.emit(
            tr(f"Parsing input: {url}", f"解析输入：{url}", f"入力を解析中: {url}")
        )

        parsed = self._parse_iwara_url(url)
        if parsed:
            kind, value = parsed
            if kind == "video":
                signal_bus.log_message.emit(
                    tr(
                        f"[Detected] Video URL -> video_id={value}",
                        f"[识别] 视频链接 → video_id={value}",
                        f"[検出] 動画URL -> video_id={value}",
                    )
                )
            elif kind == "user":
                signal_bus.log_message.emit(
                    tr(
                        f"[Detected] User URL -> username={value}",
                        f"[识别] 用户链接 → username={value}",
                        f"[検出] ユーザーURL -> username={value}",
                    )
                )
            elif kind == "playlist":
                signal_bus.log_message.emit(
                    tr(
                        f"[Detected] Playlist URL -> playlist_id={value}",
                        f"[识别] 播放列表链接 → playlist_id={value}",
                        f"[検出] プレイリストURL -> playlist_id={value}",
                    )
                )
            elif kind == "search":
                signal_bus.log_message.emit(
                    tr(
                        "[Detected] API search URL",
                        "[识别] API 搜索链接",
                        "[検出] API 検索URL",
                    )
                )
            if kind == "video":
                self._enqueue_video_id(value, url, rule_id=rule_id)
                return
            if kind == "user":
                self._enqueue_user(value, rule_id=rule_id)
                return
            if kind == "playlist":
                self._enqueue_playlist(value, rule_id=rule_id)
                return
            if kind == "search":
                self._enqueue_search_query(value, rule_id=rule_id)
                return

        # Treat as raw video ID
        signal_bus.log_message.emit(
            tr(
                f"[Detected] Treat as raw video ID -> {url}",
                f"[识别] 按视频ID处理 → {url}",
                f"[検出] 生の動画IDとして処理 -> {url}",
            )
        )
        self._enqueue_video_id(url, url, rule_id=rule_id)

    def _parse_and_mark_downloaded(self, raw: str, rule_id: str = ""):
        url = raw.strip()
        if not url:
            return
        signal_bus.log_message.emit(
            tr(
                f"[Mark downloaded] parsing input: {url}",
                f"[标记已下载] 解析输入：{url}",
                f"[保存済みマーク] 入力を解析中: {url}",
            )
        )

        parsed = self._parse_iwara_url(url)
        items: list[dict[str, Any]] = []
        if parsed:
            kind, value = parsed
            if kind == "video":
                video_info, err = self._api_call("get_video_info", str(value))
                if video_info:
                    items = [video_info]
                else:
                    signal_bus.log_message.emit(
                        tr(
                            f"[Mark downloaded] video info failed, fallback to id only: {err}",
                            f"[标记已下载] 获取视频信息失败，退回为仅按 ID 标记：{err}",
                            f"[保存済みマーク] 動画情報取得失敗、IDのみで記録: {err}",
                        )
                    )
                    items = [{"video_id": str(value), "source_url": url}]
            elif kind == "user":
                user_id, err = self._api_call("get_user_id", str(value))
                if not user_id:
                    signal_bus.log_message.emit(
                        tr(
                            f"[Mark downloaded] failed to get user id: {err}",
                            f"[标记已下载] 无法获取用户 ID：{err}",
                            f"[保存済みマーク] ユーザーID取得失敗: {err}",
                        )
                    )
                    return
                items = self._api_call("get_user_videos", user_id)
            elif kind == "playlist":
                items = self._api_call("get_playlist_videos", str(value))
            elif kind == "search":
                configured_cap = (
                    max(0, int(app_config.search_limit_count))
                    if app_config.search_limit_enabled
                    else 0
                )
                videos, err = self._api_call(
                    "get_videos_by_query",
                    value,
                    max_results=configured_cap,
                )
                if err:
                    signal_bus.log_message.emit(
                        tr(
                            f"[Mark downloaded] search returned partial result: {err}",
                            f"[标记已下载] 搜索返回部分结果：{err}",
                            f"[保存済みマーク] 検索は部分結果を返しました: {err}",
                        )
                    )
                items = videos
        else:
            video_info, err = self._api_call("get_video_info", url)
            items = [video_info] if video_info else [{"video_id": url, "source_url": url}]
            if err and not video_info:
                signal_bus.log_message.emit(
                    tr(
                        f"[Mark downloaded] video info failed, fallback to id only: {err}",
                        f"[标记已下载] 获取视频信息失败，退回为仅按 ID 标记：{err}",
                        f"[保存済みマーク] 動画情報取得失敗、IDのみで記録: {err}",
                    )
                )

        marked = self.mark_video_items_downloaded(items)
        signal_bus.log_message.emit(
            tr(
                f"[Mark downloaded] done: {marked} videos",
                f"[标记已下载] 完成：{marked} 个视频",
                f"[保存済みマーク] 完了: {marked} 件",
            )
        )

    def _parse_iwara_url(
        self, raw_url: str
    ) -> tuple[str, str | dict[str, str]] | None:
        """Parse iwara URLs into ('video'|'user'|'playlist'|'search', value)."""
        normalized = raw_url.strip()
        if "iwara.tv" in normalized and "://" not in normalized:
            normalized = f"https://{normalized.lstrip('/')}"

        try:
            parsed = urlparse(normalized)
        except Exception:
            return None

        host = (parsed.netloc or "").lower()
        if host and "iwara.tv" not in host:
            return None

        parts = [p for p in parsed.path.split("/") if p]
        if not parts:
            return None
        lower_parts = [p.lower() for p in parts]

        # Search endpoint, e.g.
        # - https://api.iwara.tv/videos?tags=2d&sort=date
        # - https://www.iwara.tv/videos?tags=2d&sort=date
        if lower_parts[0] == "videos":
            query_raw = parse_qs(parsed.query, keep_blank_values=False)
            query_params = {
                key: values[-1].strip()
                for key, values in query_raw.items()
                if values and values[-1].strip()
            }
            # Keep behavior sane for bare /videos links.
            if not query_params:
                query_params["sort"] = "date"
            return "search", query_params

        def _next_after(key: str) -> str | None:
            try:
                idx = lower_parts.index(key)
            except ValueError:
                return None
            if idx + 1 >= len(parts):
                return None
            return parts[idx + 1]

        video_id = _next_after("video")
        if video_id:
            return "video", video_id

        playlist_id = _next_after("playlist")
        if playlist_id:
            return "playlist", playlist_id

        username = _next_after("user") or _next_after("profile")
        if username:
            return "user", username

        return None

    def _enqueue_video_id(self, video_id: str, original_url: str, *, rule_id: str = ""):
        self._enqueue_video_ids_bulk([(video_id, original_url)], rule_id=rule_id)

    def _enqueue_video_ids_bulk(
        self,
        items: list[tuple[str, str]],
        *,
        source_label: str = "",
        priority: int = 0,
        rule_id: str = "",
    ) -> dict[str, int]:
        rule_id = self._normalize_rule_id(rule_id)
        rule_payload_json = json.dumps(
            self._rule_payload_for_id(rule_id),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        seen_input: set[str] = set()
        normalized: list[tuple[str, str]] = []
        skipped_empty = 0
        skipped_input_duplicate = 0
        for raw_video_id, original_url in items:
            video_id = str(raw_video_id or "").strip()
            if not video_id:
                skipped_empty += 1
                continue
            key = video_id.lower()
            if key in seen_input:
                skipped_input_duplicate += 1
                continue
            seen_input.add(key)
            normalized.append((video_id, original_url or f"https://www.iwara.tv/video/{video_id}"))

        existing_paths: dict[str, str] = {}
        if app_config.skip_existing_files and normalized:
            history_records = self.history.get_records([video_id for video_id, _ in normalized])
            file_index = self._get_existing_file_index()
            for video_id, _ in normalized:
                record = history_records.get(video_id)
                if record:
                    file_path = str(record.get("file_path", "") or "")
                    if file_path and os.path.isfile(file_path) and os.path.getsize(file_path) > 0:
                        existing_paths[video_id.lower()] = file_path
                        continue
                existing = file_index.get(video_id.lower())
                if existing and os.path.isfile(existing):
                    existing_paths[video_id.lower()] = existing

        added_infos: list[dict[str, Any]] = []
        skipped_existing = len(existing_paths)
        skipped_duplicate = 0
        queued_count = 0
        active_count = 0
        limit = app_config.max_concurrent
        with self._lock:
            for video_id, original_url in normalized:
                key = video_id.lower()
                if key in existing_paths:
                    continue
                if key in self._task_id_by_video_id:
                    skipped_duplicate += 1
                    continue
                task_id = str(uuid.uuid4())
                task = DownloadTask(
                    task_id=task_id,
                    url=original_url,
                    video_id=video_id,
                    priority=max(-100, min(100, int(priority))),
                    rule_id=str(rule_id or ""),
                    rule_payload_json=rule_payload_json,
                )
                self._tasks[task_id] = task
                self._task_id_by_video_id[key] = task_id
                self._queued_meta_ids.append(task_id)
                added_infos.append(
                    {
                        "task_id": task_id,
                        "video_id": video_id,
                        "title": video_id,
                        "author": "",
                        "status": TaskStatus.QUEUED_META.value,
                        "priority": task.priority,
                    }
                )
            queued_count = len(self._queued_meta_ids)
            active_count = len(self._active_task_ids)

        if added_infos:
            signal_bus.tasks_added.emit(added_infos)
            if len(added_infos) == 1:
                info = dict(added_infos[0])
                task_id = str(info.pop("task_id"))
                signal_bus.task_added.emit(task_id, info)

        label = f" from {source_label}" if source_label else ""
        signal_bus.log_message.emit(
            tr(
                f"[Queue] added {len(added_infos)}{label}; duplicate {skipped_duplicate + skipped_input_duplicate}; existing {skipped_existing}; queued {queued_count}; active {active_count}/{limit}",
                f"[入队] 新增 {len(added_infos)} 个{('，来源: ' + source_label) if source_label else ''}；重复 {skipped_duplicate + skipped_input_duplicate}；本地已存在 {skipped_existing}；排队 {queued_count}；活动 {active_count}/{limit}",
                f"[キュー] 追加 {len(added_infos)} 件{label}; 重複 {skipped_duplicate + skipped_input_duplicate}; 既存 {skipped_existing}; 待機 {queued_count}; 稼働 {active_count}/{limit}",
            )
        )
        if added_infos:
            self._schedule_task_persist()
        self._try_activate()
        return {
            "queued": len(added_infos),
            "duplicates": skipped_duplicate + skipped_input_duplicate,
            "existing": skipped_existing,
            "empty": skipped_empty,
        }

    def _enqueue_user(self, username: str, *, rule_id: str = ""):
        signal_bus.log_message.emit(
            tr(
                f"Fetching videos for user [{username}] ...",
                f"正在获取用户 [{username}] 的视频列表…",
                f"ユーザー [{username}] の動画一覧を取得中...",
            )
        )
        user_id, err = self._api_call("get_user_id", username)
        if not user_id:
            signal_bus.log_message.emit(
                tr(
                    f"[Error] Failed to get user ID: {err}",
                    f"[错误] 无法获取用户 ID: {err}",
                    f"[エラー] ユーザーIDの取得に失敗: {err}",
                )
            )
            return
        videos = self._api_call("get_user_videos", user_id)
        signal_bus.log_message.emit(
            tr(
                f"Found {len(videos)} videos for user [{username}]",
                f"用户 [{username}] 共找到 {len(videos)} 个视频",
                f"ユーザー [{username}] で {len(videos)} 件の動画を検出",
            )
        )
        self._enqueue_video_ids_bulk(
            [
                (str(video.get("id", "") or "").strip(), f"https://www.iwara.tv/video/{str(video.get('id', '') or '').strip()}")
                for video in videos
                if str(video.get("id", "") or "").strip()
            ],
            source_label=username,
            rule_id=rule_id,
        )

    def _enqueue_playlist(self, playlist_id: str, *, rule_id: str = ""):
        signal_bus.log_message.emit(
            tr(
                f"Fetching videos from playlist [{playlist_id}] ...",
                f"正在获取播放列表 [{playlist_id}] 的视频…",
                f"プレイリスト [{playlist_id}] の動画を取得中...",
            )
        )
        videos = self._api_call("get_playlist_videos", playlist_id)
        signal_bus.log_message.emit(
            tr(
                f"Found {len(videos)} videos in playlist",
                f"播放列表共找到 {len(videos)} 个视频",
                f"プレイリストで {len(videos)} 件の動画を検出",
            )
        )
        self._enqueue_video_ids_bulk(
            [
                (str(video.get("id", "") or "").strip(), f"https://www.iwara.tv/video/{str(video.get('id', '') or '').strip()}")
                for video in videos
                if str(video.get("id", "") or "").strip()
            ],
            source_label=tr("Playlist", "播放列表", "プレイリスト"),
            rule_id=rule_id,
        )

    def _enqueue_search_query(self, query_params: dict[str, str], *, rule_id: str = ""):
        if not query_params:
            signal_bus.log_message.emit(
                tr(
                    "[Error] Empty API query parameters",
                    "[错误] API 搜索参数为空",
                    "[エラー] API 検索パラメータが空です",
                )
            )
            return

        configured_cap = (
            max(0, int(app_config.search_limit_count))
            if app_config.search_limit_enabled
            else 0
        )
        query_text = "&".join(f"{k}={v}" for k, v in query_params.items())
        if configured_cap > 0:
            signal_bus.log_message.emit(
                tr(
                    f"Fetching search videos: {query_text} (cap: {configured_cap})",
                    f"正在拉取搜索结果: {query_text}（上限: {configured_cap}）",
                    f"検索結果を取得中: {query_text}（上限: {configured_cap}）",
                )
            )
        else:
            signal_bus.log_message.emit(
                tr(
                    f"Fetching search videos: {query_text}",
                    f"正在拉取搜索结果: {query_text}",
                    f"検索結果を取得中: {query_text}",
                )
            )

        videos, err = self._api_call(
            "get_videos_by_query",
            query_params,
            max_results=configured_cap,
        )
        if err:
            signal_bus.log_message.emit(
                tr(
                    f"[Warning] Search API returned partial result: {err}",
                    f"[警告] 搜索 API 返回部分结果: {err}",
                    f"[警告] 検索 API は部分結果を返しました: {err}",
                )
            )
        if not videos:
            signal_bus.log_message.emit(
                tr(
                    "No videos found for this query",
                    "该查询未找到视频",
                    "このクエリでは動画が見つかりませんでした",
                )
            )
            return

        signal_bus.log_message.emit(
            tr(
                f"Search found {len(videos)} videos",
                f"搜索共找到 {len(videos)} 个视频",
                f"検索で {len(videos)} 件の動画を検出",
            )
        )
        self._enqueue_video_ids_bulk(
            [
                (str(video.get("id", "") or "").strip(), f"https://www.iwara.tv/video/{str(video.get('id', '') or '').strip()}")
                for video in videos
                if str(video.get("id", "") or "").strip()
            ],
            source_label=tr("Search", "搜索", "検索"),
            rule_id=rule_id,
        )

# ── Module-level singleton ────────────────────────────────────────────────────

download_manager = DownloadManager(task_store=TaskQueueStore())
