"""Videos waiting on the download workbench for the user to confirm them.

Every way of adding downloads puts the videos in the task list as STAGED,
already pre-checked: ready to download, skipped (already here, or stopped by
the rule) or not downloadable (private and similar).  Nothing is fetched
until the user confirms a batch; the rule can be changed before that and the
batch is re-checked at once, offline.
"""

from __future__ import annotations

import json
import random
import uuid
from typing import Any

from ...i18n import tr
from ...signal_bus import signal_bus
from ..models import DownloadTask, TaskStatus
from ..rules import rule_store
from .listing_preview import HAVE_FILE, HAVE_HISTORY, HAVE_QUEUED


STAGE_READY = "ready"
STAGE_SKIP = "skip"
STAGE_UNAVAILABLE = "unavailable"


def stage_verdict(item: dict[str, Any]) -> tuple[str, str]:
    """``(STAGE_*, reason)`` for one pre-checked video."""

    if item.get("unavailable"):
        return STAGE_UNAVAILABLE, str(item["unavailable"])
    have, detail = str(item.get("have", "") or ""), str(item.get("have_detail", "") or "")
    if have == HAVE_FILE:
        return STAGE_SKIP, tr(f"Already downloaded: {detail}", f"已有文件：{detail}", f"保存済み: {detail}")
    if have == HAVE_QUEUED:
        return STAGE_SKIP, tr("Already in the task list", "已在任务列表中", "タスク一覧にあります")
    if have == HAVE_HISTORY:
        return STAGE_SKIP, tr(
            f"Downloaded before ({detail}); the file has moved",
            f"之前下载过（{detail}），文件已移走",
            f"以前に保存（{detail}）。ファイルは移動済み",
        )
    reason = str(item.get("match_reason", "") or "")
    if item.get("matches") is False:
        return STAGE_SKIP, tr(f"Stopped by the rule: {reason}", f"被规则拦住：{reason}", f"ルールで除外: {reason}")
    return STAGE_READY, reason  # a None match is checked again when it downloads


def _apply_item(task: DownloadTask, item: dict[str, Any], *, batch_id: str, label: str, rule_id: str):
    verdict, reason = stage_verdict(item)
    task.title = str(item.get("title", "") or task.title or task.video_id)
    task.author = str(item.get("author", "") or task.author)
    task.username = str(item.get("username", "") or task.username)
    task.published_at = str(item.get("published_at", "") or task.published_at)
    task.likes = int(item.get("likes", 0) or 0)
    task.views = int(item.get("views", 0) or 0)
    task.rule_id = rule_id
    task.batch_id = batch_id
    task.batch_label = label
    task.stage_verdict = verdict
    task.stage_reason = reason
    task.stage_path = str(item.get("relative_path", "") or "")
    task.stage_raw_json = json.dumps(item.get("raw") or {"id": task.video_id}, ensure_ascii=False)


class StagingMixin:
    """Videos waiting on the download workbench for the user to confirm them."""

    # ── adding ───────────────────────────────────────────────────────────────

    def stage_preview(self, preview: dict[str, Any], *, direct_single: bool = False) -> dict[str, Any]:
        """Put a pre-checked preview on the workbench as one batch.

        With ``direct_single`` a lone downloadable video skips the wait and is
        queued (or marked, under a mark-only rule) straight away.
        """

        if preview.get("error"):
            return {"error": str(preview["error"]), "staged": 0}
        items = list(preview.get("items") or [])
        rule_id = str(preview.get("rule_id", "") or "")
        label = str(preview.get("label", "") or "") or tr("Videos", "作品", "動画")
        if direct_single and len(items) == 1 and stage_verdict(items[0])[0] == STAGE_READY:
            return self._download_now(items[0], preview, label)
        batch_id = uuid.uuid4().hex[:12]
        added, updated, in_list = self._stage_items(items, batch_id=batch_id, label=label, rule_id=rule_id)
        if added:
            signal_bus.tasks_added.emit(added)
        for task_id in updated:
            signal_bus.task_status_changed.emit(task_id, TaskStatus.STAGED.value)
        if added or updated:
            self._schedule_task_persist()
            signal_bus.staging_changed.emit()
        counts = self.staged_counts(batch_id)
        return {"batch_id": batch_id, "label": label, "staged": len(added) + len(updated), "in_list": in_list, **counts}

    def _download_now(self, item: dict[str, Any], preview: dict[str, Any], label: str) -> dict[str, Any]:
        if preview.get("mark_only"):
            marked = self.mark_video_items_downloaded([item.get("raw") or {"id": item["video_id"]}])
            return {"direct": True, "marked": marked, "queued": 0, "staged": 0}
        queued = self.enqueue_video_ids([str(item["video_id"])], source_label=label, rule_id=str(preview.get("rule_id") or ""))
        return {"direct": True, "queued": queued, "marked": 0, "staged": 0}

    def _stage_items(self, items, *, batch_id: str, label: str, rule_id: str):
        """Create or refresh STAGED tasks; returns ``(added infos, updated ids, already in the list)``."""

        added: list[dict[str, Any]] = []
        updated: list[str] = []
        in_list = 0
        with self._lock:
            for item in items:
                video_id = str(item.get("video_id", "") or "").strip()
                if not video_id:
                    continue
                key = video_id.lower()
                existing = self._tasks.get(self._task_id_by_video_id.get(key, ""))
                if existing is not None and existing.status != TaskStatus.STAGED:
                    in_list += 1  # queued, running or finished already: leave it be
                    continue
                if existing is not None:
                    _apply_item(existing, item, batch_id=batch_id, label=label, rule_id=rule_id)
                    updated.append(existing.task_id)
                    continue
                task = DownloadTask(
                    task_id=str(uuid.uuid4()),
                    url=f"https://www.iwara.tv/video/{video_id}",
                    video_id=video_id,
                    status=TaskStatus.STAGED,
                )
                _apply_item(task, item, batch_id=batch_id, label=label, rule_id=rule_id)
                self._tasks[task.task_id] = task
                self._task_id_by_video_id[key] = task.task_id
                added.append({
                    "task_id": task.task_id, "video_id": video_id, "title": task.title,
                    "author": task.author, "status": TaskStatus.STAGED.value, "priority": 0,
                })
        return added, updated, in_list

    # ── reading ──────────────────────────────────────────────────────────────

    def _staged_locked(self, batch_id: str = "", task_ids=None) -> list[DownloadTask]:
        wanted = set(task_ids) if task_ids is not None else None
        return [
            task for task in self._tasks.values()
            if task.status == TaskStatus.STAGED
            and (not batch_id or task.batch_id == batch_id)
            and (wanted is None or task.task_id in wanted)
        ]

    def staged_counts(self, batch_id: str = "") -> dict[str, int]:
        with self._lock:
            tasks = self._staged_locked(batch_id)
        return {
            "total": len(tasks),
            "ready": sum(1 for t in tasks if t.stage_verdict == STAGE_READY),
            "skip": sum(1 for t in tasks if t.stage_verdict == STAGE_SKIP),
            "unavailable": sum(1 for t in tasks if t.stage_verdict == STAGE_UNAVAILABLE),
        }

    def staged_batches(self) -> list[dict[str, Any]]:
        """Batches in the order they arrived, each with its rule and counts."""

        with self._lock:
            tasks = self._staged_locked()
        batches: dict[str, dict[str, Any]] = {}
        for task in tasks:
            batch = batches.setdefault(task.batch_id, {
                "batch_id": task.batch_id, "label": task.batch_label, "rule_id": task.rule_id,
                "total": 0, "ready": 0, "skip": 0, "unavailable": 0,
            })
            batch["total"] += 1
            batch[task.stage_verdict if task.stage_verdict in (STAGE_READY, STAGE_SKIP, STAGE_UNAVAILABLE) else STAGE_SKIP] += 1
        return list(batches.values())

    def staged_example(self, batch_id: str = "") -> dict[str, str]:
        """A random video that would download: its title and where it would be saved."""

        with self._lock:
            ready = [t for t in self._staged_locked(batch_id) if t.stage_verdict == STAGE_READY and t.stage_path]
        if not ready:
            return {}
        task = random.choice(ready)
        return {"title": task.title or task.video_id, "relative_path": task.stage_path}

    # ── changing ─────────────────────────────────────────────────────────────

    def restage(self, rule_id: str, *, batch_id: str = "") -> int:
        """Re-check staged videos under ``rule_id`` (no network); returns how many."""

        with self._lock:
            tasks = self._staged_locked(batch_id)
        if not tasks:
            return 0
        payload = self._rule_payload_for_id(rule_id)
        videos = []
        for task in tasks:
            try:
                raw = json.loads(task.stage_raw_json or "{}")
            except ValueError:
                raw = {}
            videos.append(raw if isinstance(raw, dict) and raw.get("id") else {"id": task.video_id, "title": task.title})
        items = {str(item["video_id"]).lower(): item for item in self._preview_items(videos, payload)}
        rule_id = self._normalize_rule_id(rule_id)
        with self._lock:
            for task in tasks:
                item = items.get(task.video_id.lower())
                if item is not None and task.status == TaskStatus.STAGED:
                    _apply_item(task, item, batch_id=task.batch_id, label=task.batch_label, rule_id=rule_id)
        for task in tasks:
            signal_bus.task_status_changed.emit(task.task_id, TaskStatus.STAGED.value)
        self._schedule_task_persist()
        signal_bus.staging_changed.emit()
        return len(tasks)

    def force_staged_ready(self, task_ids: list[str]) -> int:
        """Let skipped videos through anyway (not the ones the site will not give)."""

        changed = []
        with self._lock:
            for task in self._staged_locked(task_ids=task_ids):
                if task.stage_verdict == STAGE_SKIP:
                    task.stage_verdict = STAGE_READY
                    task.stage_reason = tr("Let through by hand", "已手动放行", "手動で許可")
                    changed.append(task.task_id)
        for task_id in changed:
            signal_bus.task_status_changed.emit(task_id, TaskStatus.STAGED.value)
        if changed:
            self._schedule_task_persist()
            signal_bus.staging_changed.emit()
        return len(changed)

    def confirm_staged(self, *, batch_id: str = "", task_ids: list[str] | None = None) -> dict[str, int]:
        """Queue the ready videos (or mark them, under a mark-only rule).

        Confirming a whole batch (or everything) also clears its skipped and
        unavailable rows; confirming picked rows leaves the rest waiting.
        """

        with self._lock:
            scope = self._staged_locked(batch_id, task_ids)
            ready = [t for t in scope if t.stage_verdict == STAGE_READY]
        result = {"queued": 0, "marked": 0, "dropped": 0}
        by_rule: dict[str, list[DownloadTask]] = {}
        for task in ready:
            by_rule.setdefault(task.rule_id, []).append(task)
        for rule_id, tasks in by_rule.items():
            payload = self._rule_payload_for_id(rule_id)
            if payload.get("mark_submitted_as_downloaded") or not payload.get("download_video_file", True):
                result["marked"] += self._mark_staged(tasks)
                continue
            result["queued"] += self.enqueue_video_ids(
                [t.video_id for t in tasks], source_label=tasks[0].batch_label, rule_id=rule_id,
            )
        if task_ids is None:
            result["dropped"] = self.discard_staged(batch_id=batch_id)
        signal_bus.staging_changed.emit()
        return result

    def _mark_staged(self, tasks: list[DownloadTask]) -> int:
        """A mark-only rule: record them (cover / NFO / history as the rule says) in the background."""

        ids = [t.video_id for t in tasks]
        self.discard_staged(task_ids=[t.task_id for t in tasks])
        self._parse_executor.submit(self._process_subscription_items_metadata_only, ids, rule_id=tasks[0].rule_id)
        return len(ids)

    def discard_staged(self, *, batch_id: str = "", task_ids: list[str] | None = None) -> int:
        """Drop videos still waiting for confirmation; returns how many."""

        with self._lock:
            removed = [t.task_id for t in self._staged_locked(batch_id, task_ids) if self._forget_task_locked(t.task_id)]
        if removed:
            signal_bus.tasks_removed.emit(removed)
            self._schedule_task_persist()
            signal_bus.staging_changed.emit()
        return len(removed)

    def staged_rule_name(self, rule_id: str) -> str:
        rule = rule_store.find(self._normalize_rule_id(rule_id)) or {}
        return str(rule.get("name", "") or rule_id)
