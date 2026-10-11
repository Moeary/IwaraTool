"""Track task activity and cancel tasks that stopped making progress."""

from __future__ import annotations

import time

from ...config import app_config
from ...i18n import tr
from ...signal_bus import signal_bus
from ..manager_state import (
    CANCEL_ORIGIN_AUTO_STALL as _CANCEL_ORIGIN_AUTO_STALL,
    STALL_WATCHDOG_INTERVAL_SECONDS as _STALL_WATCHDOG_INTERVAL_SECONDS,
    STALL_WATCH_STATUSES as _STALL_WATCH_STATUSES,
)
from ..models import TaskStatus


class StallWatchdogMixin:
    """Track task activity and cancel tasks that stopped making progress."""

    def _is_cancel_requested(self, task_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            return bool(task and task.cancel_requested)

    def _touch_task_activity(self, task_id: str):
        with self._lock:
            self._touch_task_activity_locked(task_id)

    def _touch_task_activity_locked(self, task_id: str):
        task = self._tasks.get(task_id)
        if task and task.status in _STALL_WATCH_STATUSES and not task.cancel_requested:
            self._task_last_activity[task_id] = time.monotonic()

    def _stall_watchdog_loop(self):
        while not self._watchdog_stop.wait(_STALL_WATCHDOG_INTERVAL_SECONDS):
            self._cancel_stale_tasks()
            self._restore_auto_stalled_cancelled_if_idle()

    def _cancel_stale_tasks(self) -> int:
        try:
            timeout_seconds = int(app_config.task_stall_timeout_seconds)
        except Exception:
            timeout_seconds = 30
        if timeout_seconds <= 0:
            return 0

        now = time.monotonic()
        stale: list[tuple[str, str, int, str]] = []
        with self._lock:
            for task_id in list(self._active_task_ids):
                task = self._tasks.get(task_id)
                if (
                    not task
                    or task.status not in _STALL_WATCH_STATUSES
                    or task.cancel_requested
                ):
                    continue
                last_activity = self._task_last_activity.get(task_id)
                if last_activity is None:
                    self._task_last_activity[task_id] = now
                    continue
                idle_seconds = int(now - last_activity)
                if idle_seconds < timeout_seconds:
                    continue
                stale.append(
                    (
                        task_id,
                        task.title or task.video_id,
                        idle_seconds,
                        task.aria2_gid,
                    )
                )
                task.cancel_requested = True
                task.cancel_origin = _CANCEL_ORIGIN_AUTO_STALL
                task.status = TaskStatus.CANCELLING
                task.speed_str = ""
                self._task_last_activity.pop(task_id, None)

        for task_id, title, idle_seconds, aria2_gid in stale:
            reason = tr(
                f"No activity for {timeout_seconds}s; auto-cancelled",
                f"超过 {timeout_seconds} 秒无响应，已自动中断",
                f"{timeout_seconds} 秒間応答がないため自動中断しました",
            )
            signal_bus.log_message.emit(
                tr(
                    f"[Auto-cancel] \"{title}\" idle {idle_seconds}s",
                    f"[自动中断] 《{title}》已无响应 {idle_seconds} 秒",
                    f"[自動中断] 「{title}」応答なし {idle_seconds} 秒",
                )
            )
            self._cancel_task_terminal(task_id, reason, origin=_CANCEL_ORIGIN_AUTO_STALL)
            if aria2_gid:
                self._aria2_rpc_cancel(aria2_gid)
        return len(stale)
