"""Repair Center state (docs/flet_ui_plan.md 7.9): the form, the scanned items, the run and its log.

Runs go through ``core.repair.runs`` in a worker thread with a
``CancelToken``; batches of finished rows come back with
``call_soon_threadsafe``.  Option changes re-plan the targets (debounced)
while nothing runs.  Lives on the shell, so leaving the page keeps a run going.
"""
from __future__ import annotations

import asyncio
import contextvars
import os
from dataclasses import dataclass, field, replace
from typing import Any

import flet as ft

from ...core.cancellation import CancelToken, cancel_scope
from ...core.repair import runs
from ...core.repair.matching import parse_manual_video_id
from ...core.rules import active_rule_id, normalize_rule_payload, rule_store
from ...logging_setup import get_logger
from ...presentation.repair import page as rp
from ..services.notify import notice

logger = get_logger(__name__)

STEP_FOLDER, STEP_OPTIONS, STEP_PREVIEW, STEP_RUN, STEP_DONE = range(5)
MAX_LOG = 2000


@ft.observable
@dataclass
class RepairState:
    step: int = STEP_FOLDER
    form: rp.RepairForm = field(default_factory=rp.RepairForm)
    rule_id: str = ""
    revision: int = 0  # items changed
    busy: str = ""  # "scan" | "repair" | "resolve" | "preview" | ""
    status: str = ""
    done: int = 0
    total: int = 0
    log_revision: int = 0
    result: dict = field(default_factory=dict)


class RepairStore:
    def __init__(self, shell, manager: Any):
        self.shell = shell
        self.manager = manager
        self.state = RepairState(status=rp.idle_status())
        self.items: list[dict[str, Any]] = []
        self.by_path: dict[str, dict[str, Any]] = {}
        self.log: list[str] = []
        self.rules: list[dict[str, Any]] = []
        self.token: CancelToken | None = None
        self._preview_handle: asyncio.TimerHandle | None = None
        self._preview_generation = 0
        self.loaded = False

    # ── setup ────────────────────────────────────────────────────────────────

    def load(self) -> None:
        self.reload_rules()
        if not self.loaded:
            active = self.rule(active_rule_id())
            template = str((active or {}).get("payload", {}).get("filename_template", "") or "")
            self.state.form = rp.saved_form(template)
            self.state.rule_id = str((active or {}).get("id", ""))
            self.loaded = True

    def reload_rules(self, *_args) -> None:
        self.rules = [dict(entry, payload=normalize_rule_payload(entry.get("payload"))) for entry in rule_store.list_available()]
        self.state.revision += 1

    def rule(self, rule_id: str) -> dict[str, Any] | None:
        return next((r for r in self.rules if str(r.get("id", "")) == rule_id), None)

    def rule_choices(self) -> list[tuple[str, str]]:
        return [(str(r.get("id", "")), rp.rule_name(r)) for r in self.rules]

    # ── the form ─────────────────────────────────────────────────────────────

    def update(self, **changes) -> None:
        old = self.state.form
        form = replace(old, **changes)
        self.state.form = form
        if form.mode != old.mode:
            rp.save_mode(form.mode)
        if self.items and not self.state.busy and form.options() != old.options():
            self._schedule_preview()

    def choose_rule(self, rule_id: str) -> None:
        rule = self.rule(rule_id)
        self.state.rule_id = rule_id
        if rule:
            self.update(template=str(rule["payload"].get("filename_template", "") or ""))

    def go(self, step: int) -> None:
        if not self.state.busy:
            self.state.step = step

    # ── log ──────────────────────────────────────────────────────────────────

    def add_log(self, line: str) -> None:
        self.log.append(line)
        if len(self.log) > MAX_LOG:
            del self.log[: len(self.log) - MAX_LOG]
        self.state.log_revision += 1

    def clear_log(self) -> None:
        self.log.clear()
        self.state.log_revision += 1

    # ── running ──────────────────────────────────────────────────────────────

    def _set_items(self, items: list[Any]) -> None:
        self.items, self.by_path = rp.index_items(items)
        self.state.revision += 1

    def _batch(self, batch: list[dict[str, Any]]) -> None:
        if rp.merge_batch(self.by_path, batch):
            self.items = list(self.by_path.values())
            self.state.revision += 1

    def _progress(self, index: int, total: int) -> None:
        self.state.done, self.state.total = index, total
        text = rp.progress_status(self.state.busy == "scan", index, total)
        if text:
            self.state.status = text

    def _activity(self, completed: int, total: int, active: int) -> None:
        status, log = rp.activity_status(completed, total, active)
        self.state.status = status
        self.state.done, self.state.total = completed, total
        if completed == total:
            self.add_log(log)

    def _threadsafe(self, fn):
        """``fn`` called on the loop in the page context (scans report from the manager's pool threads)."""
        loop = self.shell.loop
        context = contextvars.copy_context()

        def call(*args) -> None:
            loop.call_soon_threadsafe(fn, *args, context=context.copy())

        return call

    async def _run(self, mode: str, work) -> Any:
        self._cancel_preview()
        token = CancelToken()
        self.token = token
        self.state.busy = mode
        self.state.done = self.state.total = 0

        def body():
            with cancel_scope(token):
                return work(lambda: token.cancelled)

        try:
            return await asyncio.to_thread(body)
        except Exception as exc:
            logger.exception("Repair %s failed", mode)
            self.add_log(rp.error_log(str(exc)))
            notice(self.shell.page, "error", rp.failed_title(), str(exc))
            return None
        finally:
            if self.token is token:
                self.token = None
            self.state.busy = ""

    def stop(self) -> None:
        if self.token is not None:
            self.token.cancel("stopped")
            self.state.status = rp.stopping_status()

    async def scan(self) -> bool:
        form = self.state.form
        folder = form.source_folder
        if not folder or not os.path.isdir(folder):
            return False
        self.state.form = form = replace(form, folder=folder)
        rp.save_form(form, folder=True)
        self.items, self.by_path = [], {}
        self.state.result = {}
        self.state.revision += 1
        self.state.step = STEP_PREVIEW
        self.state.status = rp.scanning_status()
        self.add_log(rp.scan_started_log(rp.mode_label(form.mode), folder, form.output_root))
        result = await self._run("scan", lambda stop: runs.scan(
            self.manager, folder, filename_template=form.effective_template, output_root=form.output_root,
            move_to_output=form.move_to_output, options=form.options(), on_batch=self._threadsafe(self._batch),
            on_progress=self._threadsafe(self._progress), on_activity=self._threadsafe(self._activity), should_stop=stop))
        if isinstance(result, list):
            self._set_items(result)
            self.add_log(rp.scan_finished_log(self.items))
            self.state.status = rp.progress_status(True, len(self.items), len(self.items)) or self.state.status
        return True

    async def repair(self) -> bool:
        if not self.items:
            return False
        form = self.state.form
        rp.save_form(form)
        self.state.step = STEP_RUN
        self.add_log(rp.repair_started_log(len(self.items)))
        result = await self._run("repair", lambda stop: runs.repair(
            self.manager, self.items, options=form.options(), on_batch=self._threadsafe(self._batch),
            on_progress=self._threadsafe(self._progress), should_stop=stop))
        if isinstance(result, dict):
            if isinstance(result.get("items"), list):
                self._set_items(result["items"])
            self.add_log(rp.repair_finished_log(result))
            self.state.result = dict((k, v) for k, v in result.items() if k != "items")
            self.state.step = STEP_DONE
        else:
            self.state.step = STEP_PREVIEW
        return True

    async def resolve(self, path: str, text: str) -> bool:
        """Look one file up again by a typed Iwara ID or link; False when the text is not one."""
        text = text.strip()
        if not text or not parse_manual_video_id(text) or self.state.busy:
            return False
        self.add_log(rp.lookup_started_log(path, text))
        self.state.status = rp.lookup_status("looking")
        form = self.state.form
        result = await self._run("resolve", lambda _stop: runs.resolve(self.manager, self.items, path, text, options=form.options()))
        if isinstance(result, list):
            moved = str(self.by_path.get(path, {}).get("file_path", "") or "")
            self._set_items(result)
            item = self.by_path.get(path) or self.by_path.get(moved, {})
            self.state.status = rp.lookup_status("done")
            self.add_log(rp.lookup_finished_log(path, item))
        else:
            self.state.status = rp.lookup_status("failed")
        return True

    # ── target preview after option changes ──────────────────────────────────

    def _cancel_preview(self) -> None:
        if self._preview_handle is not None:
            self._preview_handle.cancel()
            self._preview_handle = None
        self._preview_generation += 1

    def _schedule_preview(self) -> None:
        self._cancel_preview()
        generation = self._preview_generation
        self._preview_handle = self.shell.loop.call_later(
            0.15, lambda: self.shell.page.run_task(self._preview, generation))

    async def _preview(self, generation: int) -> None:
        if generation != self._preview_generation or self.state.busy or not self.items:
            return
        options = self.state.form.options()
        try:
            items = await asyncio.to_thread(runs.preview, self.manager, self.items, options)
        except Exception as exc:
            logger.exception("Repair preview failed")
            self.add_log(rp.error_log(str(exc)))
            return
        if generation == self._preview_generation and not self.state.busy:
            self._set_items(items)

    def close(self) -> None:
        self._cancel_preview()
        if self.token is not None:
            self.token.cancel("closing")
