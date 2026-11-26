"""Finished-download and background notices (the classic window's tray balloons).

Terminal task events are batched for a moment (a burst of completions becomes
one notice, ``presentation/notifications.py``), their titles are read off the
loop, and the result goes to an in-app notice while the window has the focus,
otherwise to a system notification (``core/system_notify.py``) when Settings
allows it.  Phones only show in-app notices.
"""
from __future__ import annotations

import asyncio

from ...config import app_config
from ...core import system_notify
from ...logging_setup import get_logger
from ...presentation.notifications import TaskNotificationBatch, notification_message, prepare_task_notifications
from ..lifecycle import window_focused
from .notify import notice

logger = get_logger(__name__)

DEBOUNCE = 0.6


class SystemNotices:
    def __init__(self, shell, manager):
        self.shell = shell
        self.manager = manager
        self.batch = TaskNotificationBatch()
        self._handle: asyncio.TimerHandle | None = None
        self.closed = False

    # ── events (on the loop) ─────────────────────────────────────────────────

    def on_task_status(self, task_id: str = "", status: str = "", *_args) -> None:
        if self.closed or not self.batch.add(task_id, status):
            return
        if self._handle is None:
            self._handle = self.shell.loop.call_later(DEBOUNCE, self._flush)

    def _flush(self) -> None:
        self._handle = None
        events = self.batch.drain()
        if events and not self.closed:
            self.shell.page.run_task(self._present_tasks, events)

    async def _present_tasks(self, events) -> None:
        try:
            prepared = await asyncio.to_thread(prepare_task_notifications, events, self.manager.get_task)
        except Exception:
            logger.exception("Preparing download notices failed")
            return
        message = notification_message(prepared)
        if message is not None:
            await self.show(*message)

    async def show(self, title: str, message: str) -> None:
        """A system notification while the window is in the background (when allowed), else an in-app notice."""
        if self.closed:
            return
        shell = self.shell
        in_front = not shell.caps.desktop or window_focused(shell.page)
        if not in_front and app_config.desktop_notifications_enabled:
            if await asyncio.to_thread(system_notify.notify, title, message):
                return
        notice(shell.page, "info", title, message, duration_ms=5000)  # in front, or waiting for their return

    def close(self) -> None:
        self.closed = True
        if self._handle is not None:
            self._handle.cancel()
            self._handle = None
        self.batch.clear()
