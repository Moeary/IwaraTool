"""Small, thread-safe-by-ownership helpers for task notifications.

The helpers in this module deliberately contain no Qt widgets.  A completed
download can arrive from any worker thread, so the event data is collected
first and only the final presentation is handed back to the Qt GUI thread.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable, Iterable


_TERMINAL_NOTIFICATION_STATUSES = frozenset({"completed", "failed"})


@dataclass(frozen=True)
class TaskNotificationEvent:
    """A terminal task transition waiting to be presented."""

    task_id: str
    status: str


@dataclass(frozen=True)
class PreparedTaskNotifications:
    """Immutable notification text data prepared outside the GUI thread."""

    completed_titles: tuple[str, ...] = ()
    failed_messages: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.completed_titles and not self.failed_messages


class TaskNotificationBatch:
    """Deduplicate terminal transitions and drain them as one batch.

    The owning Qt object calls :meth:`add` and :meth:`drain` from its GUI
    thread.  Keeping this state in a tiny non-Qt object makes the batching
    behavior easy to test and prevents a burst of completed tasks from
    creating one animated InfoBar per task.
    """

    def __init__(self):
        self._events: "OrderedDict[str, TaskNotificationEvent]" = OrderedDict()

    def add(self, task_id: str, status: str) -> bool:
        task_id = str(task_id or "").strip()
        status = str(status or "").strip().lower()
        if not task_id or status not in _TERMINAL_NOTIFICATION_STATUSES:
            return False
        event = TaskNotificationEvent(task_id, status)
        if self._events.get(task_id) == event:
            return False
        self._events[task_id] = event
        return True

    def drain(self) -> list[TaskNotificationEvent]:
        events = list(self._events.values())
        self._events.clear()
        return events

    def clear(self):
        self._events.clear()

    def __bool__(self) -> bool:
        return bool(self._events)

    def __len__(self) -> int:
        return len(self._events)


def prepare_task_notifications(
    events: Iterable[TaskNotificationEvent],
    task_lookup: Callable[[str], object | None],
) -> PreparedTaskNotifications:
    """Copy the small amount of task data needed by the GUI notification.

    ``task_lookup`` is intentionally injected so this function can run in a
    worker thread without importing or touching Qt.  The manager's lookup is
    lock-protected and returns the live task object; values are copied before
    the function returns.
    """

    completed: list[str] = []
    failed: list[str] = []
    for event in events:
        task = task_lookup(event.task_id)
        if task is None:
            continue
        title = str(getattr(task, "title", "") or getattr(task, "video_id", "") or event.task_id)
        if event.status == "completed":
            completed.append(title)
            continue
        error = str(getattr(task, "error_msg", "") or "").strip()
        failed.append(f"{title}: {error}" if error else title)
    return PreparedTaskNotifications(tuple(completed), tuple(failed))


def notification_message(prepared: PreparedTaskNotifications) -> tuple[str, str] | None:
    """The (title, message) one notification shows for a batch, or None when there is nothing to say."""
    from ..i18n import tr

    completed, failed = prepared.completed_titles, prepared.failed_messages
    if not completed and not failed:
        return None
    if len(completed) == 1 and not failed:
        return tr("Download completed", "下载完成", "ダウンロード完了"), completed[0]
    if len(failed) == 1 and not completed:
        return tr("Download failed", "下载失败", "ダウンロード失敗"), failed[0]
    parts: list[str] = []
    if completed:
        parts.append(tr(f"{len(completed)} downloads completed", f"{len(completed)} 个下载已完成",
                        f"{len(completed)} 件のダウンロードが完了"))
    if failed:
        parts.append(tr(f"{len(failed)} downloads failed", f"{len(failed)} 个下载失败",
                        f"{len(failed)} 件のダウンロードに失敗"))
    return tr("Download updates", "下载任务更新", "ダウンロード更新"), "；".join(parts)
