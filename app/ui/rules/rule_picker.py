"""Compact rule chooser used by the download, search and subscription pages."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QStringListModel, Qt, Signal
from PySide6.QtWidgets import QCompleter, QHBoxLayout, QWidget

from qfluentwidgets import EditableComboBox, InfoBar, InfoBarPosition

from ...core.rules import (
    BUILTIN_DEFAULT_RULE_ID,
    active_rule_id,
    apply_rule_payload,
    normalize_rule_payload,
    rule_store,
    set_active_rule_id,
)
from ...i18n import tr
from ...signal_bus import signal_bus
from .rule_text import _rule_display_name, _rule_summary


class RulePicker(QWidget):
    """Single-line Fluent searchable selector that applies a rule immediately."""

    ruleApplied = Signal(dict)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._rules_cache: list[dict[str, Any]] = []
        self._syncing = False
        self._model = QStringListModel(self)
        self.combo = EditableComboBox(self)
        self.combo.setPlaceholderText(tr("Search or select a rule…", "搜索或选择下载规则…", "ルールを検索または選択…"))
        self.combo.setMinimumWidth(240)
        self.combo.setToolTip(tr("Selecting applies it immediately", "选中后立即应用并设为默认", "選択するとすぐ適用されます"))
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
        signal_bus.active_rule_changed.connect(self._sync_active_rule)
        self.refresh_rules(apply_current=True)

    def refresh_rules(self, apply_current: bool = False):
        selected = active_rule_id()
        self._rules_cache = rule_store.list_available()
        if not any(rule["id"] == selected for rule in self._rules_cache):
            selected = BUILTIN_DEFAULT_RULE_ID
            set_active_rule_id(selected)
        self._rebuild_combo(selected)
        if apply_current:
            self._activate_rule(selected, show_notice=False, broadcast=False)

    def _rebuild_combo(self, selected: str):
        self._syncing = True
        self.combo.blockSignals(True)
        self.combo.clear()
        labels: list[str] = []
        for rule in self._rules_cache:
            suffix = tr(" · built in", " · 内置", " · 内蔵") if rule.get("builtin") else ""
            label = _rule_display_name(rule) + suffix
            labels.append(label)
            self.combo.addItem(label, userData=rule["id"])
        self._model.setStringList(labels)
        self.select_rule(selected)
        self.combo.blockSignals(False)
        self._syncing = False

    def selected_rule_id(self) -> str:
        return str(self.combo.currentData() or BUILTIN_DEFAULT_RULE_ID)

    def selected_rule(self) -> dict[str, Any] | None:
        rule_id = self.selected_rule_id()
        return next((r for r in self._rules_cache if r["id"] == rule_id), None)

    def select_rule(self, rule_id: str):
        for index in range(self.combo.count()):
            if str(self.combo.itemData(index) or "") == str(rule_id):
                self.combo.setCurrentIndex(index)
                return

    def _on_combo_changed(self, _index: int):
        if not self._syncing:
            self._activate_rule(self.selected_rule_id(), show_notice=True, broadcast=True)

    def _sync_active_rule(self, rule_id: str):
        if rule_id == self.selected_rule_id():
            return
        self._rebuild_combo(rule_id)
        rule = self.selected_rule()
        if rule:
            self.ruleApplied.emit(normalize_rule_payload(rule["payload"]))

    def _activate_rule(self, rule_id: str, *, show_notice: bool, broadcast: bool) -> bool:
        rule = next((r for r in self._rules_cache if r["id"] == rule_id), None)
        if rule is None:
            rule = self._rules_cache[0] if self._rules_cache else None
        if rule is None:
            return False
        normalized = apply_rule_payload(rule["payload"])
        set_active_rule_id(rule["id"])
        signal_bus.download_options_changed.emit()
        self.ruleApplied.emit(normalized)
        if broadcast:
            signal_bus.active_rule_changed.emit(rule["id"])
        if show_notice:
            InfoBar.success(
                title=tr("Default rule updated", "默认规则已更新", "既定ルールを更新しました"),
                content=f"{_rule_display_name(rule)} · {_rule_summary(normalized)}",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=1900,
                parent=self.window(),
            )
        return True

    def apply_selected(self, show_notice: bool = False) -> bool:
        return self._activate_rule(self.selected_rule_id(), show_notice=show_notice, broadcast=True)
