"""Shared task-state constants for DownloadManager mixins."""
from __future__ import annotations

from .models import TaskStatus


ACTIVE_STATUSES = frozenset(
    {
        TaskStatus.RESOLVING,
        TaskStatus.QUEUED_DOWNLOAD,
        TaskStatus.DOWNLOADING,
        TaskStatus.CANCELLING,
    }
)
TERMINAL_STATUSES = frozenset(
    {
        TaskStatus.COMPLETED,
        TaskStatus.SKIPPED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    }
)
PRUNABLE_TERMINAL_STATUSES = frozenset(
    {
        TaskStatus.COMPLETED,
        TaskStatus.SKIPPED,
        TaskStatus.FAILED,
    }
)
STALL_WATCH_STATUSES = frozenset(
    {
        TaskStatus.RESOLVING,
        TaskStatus.QUEUED_DOWNLOAD,
        TaskStatus.DOWNLOADING,
    }
)

LIVE_TERMINAL_KEEP_LIMIT = 100
EXISTING_FILE_INDEX_TTL_SECONDS = 60
STALL_WATCHDOG_INTERVAL_SECONDS = 1.0
CANCEL_ORIGIN_AUTO_STALL = "auto_stall"
CANCEL_ORIGIN_MANUAL = "manual"
CANCEL_ORIGIN_SHUTDOWN = "shutdown"
