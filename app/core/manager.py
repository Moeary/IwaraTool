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

import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

from ..i18n import tr
from ..signal_bus import signal_bus
from .api.client import IwaraAPI
from .download.paths import DownloadPathMixin
from .download.aria2 import Aria2DownloadMixin
from .download.artifacts import DownloadArtifactsMixin
from .download.runtime import DownloadRuntimeMixin
from .repair.manager import RepairManagerMixin
from .rules import active_rule_id  # noqa: F401 - patch point, see download.task_rules.active_rule_id
from .search_manager import SearchManagerMixin
from .subscription.manager import SubscriptionManagerMixin
from .download.policy import SharedRateLimiter
from .history import DownloadHistory
from .image_cache import SearchImageCache, SubscriptionImageCache
from .manager_state import (
    CANCEL_ORIGIN_SHUTDOWN as _CANCEL_ORIGIN_SHUTDOWN,
    LIVE_TERMINAL_KEEP_LIMIT as _LIVE_TERMINAL_KEEP_LIMIT,
    TERMINAL_STATUSES as _TERMINAL_STATUSES,
)
from .models import DownloadTask, TaskStatus
from .subscription.store import SubscriptionStore
from .tag_dictionary import TagDictionary
from .task_queue import TaskPersistenceCoordinator, TaskQueueStore
from .download.enqueueing import EnqueueingMixin
from .download.history_actions import HistoryActionsMixin
from .download.session import SessionMixin
from .download.task_control import TaskControlMixin
from .download.url_intake import UrlIntakeMixin

if TYPE_CHECKING:
    pass


class DownloadManager(
    TaskControlMixin,
    HistoryActionsMixin,
    SessionMixin,
    UrlIntakeMixin,
    EnqueueingMixin,
    DownloadRuntimeMixin,
    Aria2DownloadMixin,
    DownloadArtifactsMixin,
    SubscriptionManagerMixin,
    SearchManagerMixin,
    RepairManagerMixin,
    DownloadPathMixin,
):
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
        # Short lock for token/proxy writes. _api_lock is held across network
        # requests, so UI-thread readers must never wait on it; readers take a
        # snapshot instead (writers always replace whole values).
        self._auth_lock = threading.Lock()
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


# ── Module-level singleton ────────────────────────────────────────────────────

download_manager = DownloadManager(task_store=TaskQueueStore())
