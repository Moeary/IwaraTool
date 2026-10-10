"""The download rule a task runs under: its snapshot, filters and skipping."""

from __future__ import annotations

import json
import sys
from typing import Any

from ...i18n import tr
from ...signal_bus import signal_bus
from ..models import DownloadTask, TaskStatus
from ..rules import (
    active_rule_id as _default_active_rule_id,
    current_rule_payload,
    normalize_rule_payload,
    rule_store,
)
from ..subscription.automation import matches_rule_metadata


def active_rule_id() -> str:
    """Resolve the manager-level hook lazily for test/integration overrides."""

    manager_module = sys.modules.get("app.core.manager")
    resolver = getattr(manager_module, "active_rule_id", _default_active_rule_id)
    return str(resolver())


class TaskRulesMixin:
    """The download rule a task runs under: its snapshot, filters and skipping."""

    @staticmethod
    def _normalize_rule_id(rule_id: str | None) -> str:
        return str(rule_id or active_rule_id()).strip() or active_rule_id()

    def _rule_payload_for_id(self, rule_id: str | None = "") -> dict[str, Any]:
        selected_id = str(rule_id or "").strip()
        if selected_id:
            rule = rule_store.find(selected_id)
            if rule:
                return normalize_rule_payload(rule.get("payload"))
        return normalize_rule_payload(current_rule_payload())

    def _task_rule_payload(self, task: DownloadTask) -> dict[str, Any]:
        raw_snapshot = str(getattr(task, "rule_payload_json", "") or "").strip()
        if raw_snapshot:
            try:
                snapshot = json.loads(raw_snapshot)
            except (TypeError, ValueError, json.JSONDecodeError):
                snapshot = None
            if isinstance(snapshot, dict):
                return normalize_rule_payload(snapshot)
        return self._rule_payload_for_id(task.rule_id)

    def _passes_filters(
        self,
        title: str,
        likes: int,
        views: int,
        published_at: str,
        tags: list[Any],
        payload: dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        return matches_rule_metadata(
            title=title,
            likes=likes,
            views=views,
            published_at=published_at,
            tags=tags,
            payload=payload or current_rule_payload(),
        )

    def _skip_task(self, task_id: str, reason: str):
        if self._is_cancel_requested(task_id):
            self._cancel_task_terminal(
                task_id,
                tr("Cancelled by user", "用户已中断", "ユーザーが中断しました"),
            )
            return
        with self._lock:
            task = self._tasks.get(task_id)
            if task:
                task.status = TaskStatus.SKIPPED
                task.error_msg = reason
                self._mark_terminal_locked(task)
        signal_bus.log_message.emit(
            tr(
                f"[Filtered] {task_id} -> {reason}",
                f"[筛选跳过] {task_id} → {reason}",
                f"[フィルター] {task_id} -> {reason}",
            )
        )
        signal_bus.task_status_changed.emit(task_id, TaskStatus.SKIPPED.value)
        self._prune_terminal_tasks()
        self._schedule_task_persist()
        self._try_activate()
