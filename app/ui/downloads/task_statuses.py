"""Task status groups and their default sort priority."""

from __future__ import annotations

from ...core.models import TaskStatus


_DEFAULT_STATUS_PRIORITY: dict[TaskStatus, int] = {
    TaskStatus.STAGED: -1,  # waiting for confirmation: on top
    TaskStatus.DOWNLOADING: 0,
    TaskStatus.RESOLVING: 1,
    TaskStatus.CANCELLING: 2,
    TaskStatus.QUEUED_DOWNLOAD: 3,
    TaskStatus.QUEUED_META: 4,
    TaskStatus.FAILED: 5,
    TaskStatus.SKIPPED: 6,
    TaskStatus.CANCELLED: 7,
    TaskStatus.COMPLETED: 8,
}


_ACTIVE_STATUSES = frozenset(
    {
        TaskStatus.RESOLVING,
        TaskStatus.QUEUED_DOWNLOAD,
        TaskStatus.DOWNLOADING,
        TaskStatus.CANCELLING,
    }
)


_TERMINAL_STATUSES = frozenset(
    {
        TaskStatus.COMPLETED,
        TaskStatus.SKIPPED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    }
)
