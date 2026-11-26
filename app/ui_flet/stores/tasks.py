"""Download tasks as the Downloads page shows them (docs/flet_ui_plan.md 7.6).

Two levels so a progress tick re-renders one row, not the list:

* ``TaskIndex`` (observable): the task IDs in display order and the counts
  per status tab.  Rebuilt from a manager snapshot when tasks come or go or
  change status.
* ``TaskItem`` (observable, one per task): the task's latest copy.  Progress
  events update it in place.

``TaskStore.attach`` subscribes to the manager's events through the window's
``EventBridge``; progress is coalesced per task by the bridge.
"""
from __future__ import annotations

import asyncio
import copy
from dataclasses import dataclass, field
from typing import Any

import flet as ft

from ...core.models import DownloadTask, TaskStatus
from ...logging_setup import get_logger
from ...presentation.downloads.task_statuses import _ACTIVE_STATUSES, _DEFAULT_STATUS_PRIORITY
from ...presentation.downloads.task_text import task_search_text

logger = get_logger(__name__)

# Status tabs (7.6): label keys and the statuses each one holds.
TAB_ALL = "all"
TAB_ACTIVE = "active"
TAB_QUEUED = "queued"
TAB_FAILED = "failed"
TAB_COMPLETED = "completed"
TAB_SKIPPED = "skipped"
TAB_CANCELLED = "cancelled"
TABS: dict[str, frozenset[TaskStatus] | None] = {
    TAB_ALL: None,
    TAB_ACTIVE: frozenset({TaskStatus.RESOLVING, TaskStatus.DOWNLOADING, TaskStatus.CANCELLING}),
    TAB_QUEUED: frozenset({TaskStatus.QUEUED_META, TaskStatus.QUEUED_DOWNLOAD}),
    TAB_FAILED: frozenset({TaskStatus.FAILED}),
    TAB_COMPLETED: frozenset({TaskStatus.COMPLETED}),
    TAB_SKIPPED: frozenset({TaskStatus.SKIPPED}),
    TAB_CANCELLED: frozenset({TaskStatus.CANCELLED}),
}
LOG_LIMIT = 2000


def tab_of(status: TaskStatus) -> str:
    for tab, statuses in TABS.items():
        if statuses is not None and status in statuses:
            return tab
    return TAB_ALL


def sort_key(task: DownloadTask) -> tuple:
    """Waiting-to-confirm first, then downloading … completed; higher priority first inside a group."""
    return (_DEFAULT_STATUS_PRIORITY.get(task.status, 99), -int(task.priority or 0))


@ft.observable
@dataclass
class TaskItem:
    task: DownloadTask


@ft.observable
@dataclass
class TaskIndex:
    order: list[str] = field(default_factory=list)  # every non-staged task, display order
    counts: dict[str, int] = field(default_factory=dict)
    tab: str = TAB_ALL
    query: str = ""
    loaded: bool = False
    revision: int = 0


@ft.observable
@dataclass
class LogState:
    lines: list[str] = field(default_factory=list)
    unseen_errors: int = 0


class TaskStore:
    def __init__(self, manager: Any):
        self.manager = manager
        self.index = TaskIndex()
        self.log = LogState()
        self.items: dict[str, TaskItem] = {}
        self._search_text: dict[str, str] = {}
        self._reload_pending = False

    # ── queries ──────────────────────────────────────────────────────────────

    def visible_ids(self) -> list[str]:
        statuses = TABS.get(self.index.tab)
        query = self.index.query.strip().lower()
        result = []
        for task_id in self.index.order:
            item = self.items.get(task_id)
            if item is None:
                continue
            if statuses is not None and item.task.status not in statuses:
                continue
            if query and query not in self._search_text.get(task_id, ""):
                continue
            result.append(task_id)
        return result

    def set_tab(self, tab: str) -> None:
        self.index.tab = tab if tab in TABS else TAB_ALL

    def set_query(self, text: str) -> None:
        self.index.query = str(text or "")

    def active_count(self) -> int:
        return sum(1 for item in self.items.values() if item.task.status in _ACTIVE_STATUSES)

    # ── loading ──────────────────────────────────────────────────────────────

    def _snapshot(self) -> list[DownloadTask]:
        return [copy.copy(task) for task in self.manager.get_tasks()]

    async def reload(self) -> None:
        """Re-read every task (off the loop) and rebuild the index."""

        try:
            tasks = await asyncio.to_thread(self._snapshot)
        except Exception:
            logger.exception("Could not read the download tasks")
            return
        self.apply_snapshot(tasks)

    def apply_snapshot(self, tasks: list[DownloadTask]) -> None:
        tasks = [task for task in tasks if task.status != TaskStatus.STAGED]
        tasks.sort(key=sort_key)
        seen = set()
        for task in tasks:
            seen.add(task.task_id)
            item = self.items.get(task.task_id)
            if item is None:
                self.items[task.task_id] = TaskItem(task)
            elif _changed(item.task, task):
                item.task = task
            self._search_text[task.task_id] = task_search_text(task)
        for gone in set(self.items) - seen:
            self.items.pop(gone, None)
            self._search_text.pop(gone, None)
        counts = {tab: 0 for tab in TABS}
        counts[TAB_ALL] = len(tasks)
        for task in tasks:
            tab = tab_of(task.status)
            if tab != TAB_ALL:
                counts[tab] += 1
        order = [task.task_id for task in tasks]
        index = self.index
        if index.order != order:
            index.order = order
        if index.counts != counts:
            index.counts = counts
        index.loaded = True
        index.revision += 1

    def schedule_reload(self, page: ft.Page) -> None:
        if self._reload_pending:
            return
        self._reload_pending = True

        async def run() -> None:
            await asyncio.sleep(0.12)  # several events in a row: one snapshot
            self._reload_pending = False
            await self.reload()

        page.run_task(run)

    # ── events ───────────────────────────────────────────────────────────────

    def on_progress(self, task_id: str, downloaded: int, total: int, speed: str) -> None:
        item = self.items.get(task_id)
        if item is None:
            return
        task = copy.copy(item.task)
        task.downloaded_bytes, task.total_bytes, task.speed_str = int(downloaded), int(total), str(speed or "")
        item.task = task

    def on_log(self, line: str) -> None:
        lines = list(self.log.lines[-(LOG_LIMIT - 1):])
        lines.append(str(line))
        self.log.lines = lines
        if "error" in line.lower() or "失败" in line or "失敗" in line:
            self.log.unseen_errors += 1

    def attach(self, shell) -> None:
        events = shell.events
        reload = lambda *_args: self.schedule_reload(shell.page)  # noqa: E731
        for name in ("task_added", "tasks_added", "task_status_changed", "task_removed", "tasks_removed",
                     "task_priority_changed", "task_error", "staging_changed"):
            events.on(name, reload, coalesce=lambda *_args: "reload")
        events.on("task_progress_updated", self.on_progress, coalesce=lambda task_id, *_rest: task_id)
        events.on("log_message", self.on_log)


def _changed(old: DownloadTask, new: DownloadTask) -> bool:
    return (
        old.status != new.status
        or old.downloaded_bytes != new.downloaded_bytes
        or old.total_bytes != new.total_bytes
        or old.title != new.title
        or old.error_msg != new.error_msg
        or old.priority != new.priority
        or old.thumbnail_path != new.thumbnail_path
        or old.speed_str != new.speed_str
        or old.file_path != new.file_path
    )
