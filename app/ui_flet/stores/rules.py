"""Download rules (docs/flet_ui_plan.md 7.8): the list, the rule being edited and its form.

The form is ``rule_editing``'s flat dict; saving, deleting and making a rule
the default go through ``presentation.rule_editing`` (which applies and
announces them like the old page).  Rule files are tiny JSON, read on the loop.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import flet as ft

from ...core.rules import BUILTIN_DEFAULT_RULE_ID, active_rule_id
from ...presentation import rule_editing as rules
from ...presentation.rules import DRAFT_RULE_ID


@ft.observable
@dataclass
class RulesState:
    revision: int = 0  # the list (rules, default, page bindings) changed
    selected: str = ""
    name: str = ""
    form: dict[str, Any] = field(default_factory=dict)
    dirty: bool = False
    error: str = ""


class RulesStore:
    def __init__(self, shell):
        self.shell = shell
        self.state = RulesState()
        self.draft: dict[str, Any] | None = None
        self.rules: list[dict[str, Any]] = []
        self.active_id = ""
        self.loaded = False

    def load(self, select: str | None = None) -> None:
        self.rules = rules.list_rules(self.draft)
        self.active_id = active_rule_id()
        self.loaded = True
        target = select or self.state.selected or self.active_id
        if not any(r["id"] == target for r in self.rules):
            target = self.rules[0]["id"] if self.rules else ""
        if target != self.state.selected or select is not None or not self.state.form:
            self._show(target)
        self.state.revision += 1

    def rule(self, rule_id: str) -> dict[str, Any] | None:
        return next((r for r in self.rules if r["id"] == rule_id), None)

    @property
    def builtin(self) -> bool:
        rule = self.rule(self.state.selected)
        return bool(rule and rule.get("builtin"))

    def _show(self, rule_id: str) -> None:
        rule = self.rule(rule_id)
        state = self.state
        state.selected = rule_id
        state.name = rules.display_name(rule) if rule else ""
        state.form = rules.form_from_payload(rule["payload"]) if rule else {}
        state.dirty = rule_id == DRAFT_RULE_ID
        state.error = ""

    def select(self, rule_id: str) -> None:
        self._show(rule_id)
        self.state.revision += 1

    # ── editing ──────────────────────────────────────────────────────────────

    def set_name(self, name: str) -> None:
        self.state.name = name
        self.state.dirty = True
        if self.state.selected == DRAFT_RULE_ID and self.draft is not None:
            self.draft["name"] = name.strip() or rules.new_rule_name()

    def set_field(self, key: str, value: Any) -> None:
        form = dict(self.state.form)
        form[key] = value
        # Downloading and "mark only" exclude each other, as in the old form.
        if key == "download_video_file" and value:
            form["mark_submitted_as_downloaded"] = False
        elif key == "mark_submitted_as_downloaded" and value:
            form["download_video_file"] = False
        self.state.form = form
        self.state.dirty = True
        self.state.error = ""

    def payload(self) -> dict[str, Any]:
        return rules.payload_from_form(self.state.form)

    def summary(self) -> str:
        from ...presentation.rules import _rule_summary

        try:
            return _rule_summary(self.payload())
        except ValueError:
            return ""

    # ── list actions ─────────────────────────────────────────────────────────

    def new(self) -> None:
        self.draft = rules.new_draft()
        self.load(DRAFT_RULE_ID)

    def duplicate(self) -> None:
        try:
            payload = self.payload()
        except ValueError as exc:
            self.state.error = str(exc)
            return
        self.draft = rules.duplicate_draft(self.state.name, payload)
        self.load(DRAFT_RULE_ID)

    def save(self) -> dict[str, Any] | None:
        try:
            rule = rules.save_rule(self.state.name, self.payload(), self.state.selected)
        except ValueError as exc:
            self.state.error = str(exc)
            return None
        self.draft = None
        self.load(rule["id"])
        return rule

    def delete(self) -> bool:
        selected = self.state.selected
        if selected == DRAFT_RULE_ID:
            self.draft = None
            self.load(self.active_id)
            return True
        deleted_active = selected == self.active_id
        if not rules.delete_rule(selected):
            return False
        self.load(BUILTIN_DEFAULT_RULE_ID if deleted_active else self.active_id)
        return True

    def make_default(self) -> bool:
        if self.state.selected in ("", DRAFT_RULE_ID):
            return False
        try:
            payload = self.payload()
        except ValueError as exc:
            self.state.error = str(exc)
            return False
        rules.make_default(self.state.selected, payload)
        self.load(self.state.selected)
        return True

    def refresh(self, *_args) -> None:
        """Rules, the default or a page binding changed elsewhere: keep the edit, redraw the list."""
        if not self.loaded:
            return
        self.rules = rules.list_rules(self.draft)
        self.active_id = active_rule_id()
        self.state.revision += 1
