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


def badge_counts(manager) -> tuple[int, int]:
    """(tasks in progress, videos waiting for confirmation) for the Downloads navigation badge."""

    active = sum(1 for task in manager.get_tasks() if task.status in _ACTIVE_STATUSES)
    staged = int(manager.staged_counts().get("total", 0))
    return active, staged


def badge_text(count: int) -> str:
    return "99+" if count > 99 else str(count) if count > 0 else ""
