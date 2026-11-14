"""Compact per-page rule chooser used by the download, search, home and subscription pages."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QStringListModel, Qt
from PySide6.QtWidgets import QCompleter, QHBoxLayout, QWidget

from qfluentwidgets import EditableComboBox, InfoBar, InfoBarPosition

from ...core.rules import (
    PAGE_RULE_FOLLOW_DEFAULT,
    active_rule_id,
    normalize_rule_payload,
    page_rule_choice,
    rule_store,
    set_page_rule_choice,
)
from ...i18n import tr
from ...signal_bus import signal_bus
from .rule_text import _rule_display_name, _rule_summary, page_scope_name


class RulePicker(QWidget):
    """Single-line Fluent searchable selector for the rule one page downloads with.

    Pickers of the same ``scope`` stay in step; other pages keep their own rule.
    The first entry follows the default rule chosen on the Rules page.
    """

    def __init__(self, parent: QWidget | None = None, *, scope: str):
        super().__init__(parent)
        self.scope = scope
        self._rules_cache: list[dict[str, Any]] = []
        self._syncing = False
        self._model = QStringListModel(self)
        self.combo = EditableComboBox(self)
        self.combo.setPlaceholderText(tr("Search or select a rule…", "搜索或选择下载规则…", "ルールを検索または選択…"))
        self.combo.setMinimumWidth(240)
        self.combo.setToolTip(tr(
            f"Rule used for downloads started on the {page_scope_name(scope)} page only",
            f"只对「{page_scope_name(scope)}」发起的下载生效",
            f"「{page_scope_name(scope)}」から始めるダウンロードだけに使います",
        ))
        completer = QCompleter(self._model, self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        completer.setMaxVisibleItems(8)
        self.combo.setCompleter(completer)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.combo, 1)
        self.combo.currentIndexChanged.connect(self._on_combo_changed)
        signal_bus.rules_changed.connect(self.refresh_rules)
        signal_bus.active_rule_changed.connect(self._on_default_changed)
        signal_bus.page_rule_changed.connect(self._on_page_rule_changed)
        self.refresh_rules()

    def refresh_rules(self):
        self._rules_cache = rule_store.list_available()
        self._rebuild_combo(page_rule_choice(self.scope))

    def _rebuild_combo(self, choice: str):
        self._syncing = True
        self.combo.blockSignals(True)
        self.combo.clear()
        default_rule = self._find(active_rule_id()) or (self._rules_cache[0] if self._rules_cache else {})
        labels = [tr(
            f"Follow default · {_rule_display_name(default_rule)}",
            f"跟随默认 · {_rule_display_name(default_rule)}",
            f"既定に従う · {_rule_display_name(default_rule)}",
        )]
        self.combo.addItem(labels[0], userData=PAGE_RULE_FOLLOW_DEFAULT)
        for rule in self._rules_cache:
            suffix = tr(" · built in", " · 内置", " · 内蔵") if rule.get("builtin") else ""
            label = _rule_display_name(rule) + suffix
            labels.append(label)
            self.combo.addItem(label, userData=rule["id"])
        self._model.setStringList(labels)
        if not self._select_choice(choice):
            self.combo.setCurrentIndex(0)  # its rule was deleted: follow the default
        self.combo.blockSignals(False)
        self._syncing = False

    def _find(self, rule_id: str) -> dict[str, Any] | None:
        return next((r for r in self._rules_cache if r["id"] == rule_id), None)

    def _select_choice(self, choice: str) -> bool:
        for index in range(self.combo.count()):
            if str(self.combo.itemData(index) or "") == str(choice):
                self.combo.setCurrentIndex(index)
                return True
        return False

    def selected_rule_id(self) -> str:
        """The rule downloads from this page run with (the default when following it)."""
        choice = str(self.combo.currentData() or PAGE_RULE_FOLLOW_DEFAULT)
        if choice != PAGE_RULE_FOLLOW_DEFAULT and self._find(choice):
            return choice
        return active_rule_id()

    def selected_rule(self) -> dict[str, Any] | None:
        return self._find(self.selected_rule_id())

    def selected_payload(self) -> dict[str, Any]:
        rule = self.selected_rule()
        return normalize_rule_payload(rule["payload"] if rule else None)

    def _on_combo_changed(self, _index: int):
        if self._syncing:
            return
        choice = str(self.combo.currentData() or PAGE_RULE_FOLLOW_DEFAULT)
        set_page_rule_choice(self.scope, choice)
        signal_bus.page_rule_changed.emit(self.scope, choice)
        rule = self.selected_rule()
        if rule is None:
            return
        InfoBar.success(
            title=tr(
                f"{page_scope_name(self.scope)} rule updated",
                f"「{page_scope_name(self.scope)}」的下载规则已更新",
                f"「{page_scope_name(self.scope)}」のルールを更新しました",
            ),
            content=f"{_rule_display_name(rule)} · {_rule_summary(rule['payload'])}",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=1900,
            parent=self.window(),
        )

    def _on_page_rule_changed(self, scope: str, choice: str):
        if scope == self.scope and choice != str(self.combo.currentData() or ""):
            self._rebuild_combo(choice)

    def _on_default_changed(self, _rule_id: str):
        # The "follow default" entry names the default rule.
        self._rebuild_combo(str(self.combo.currentData() or page_rule_choice(self.scope)))
