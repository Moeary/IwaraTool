"""Named download/filter rules page and reusable rule picker."""
from __future__ import annotations

import copy
from typing import Any

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    FluentIcon,
    InfoBar,
    InfoBarPosition,
    ListWidget,
    PrimaryPushButton,
    PushButton,
    ScrollArea,
    SubtitleLabel,
    TitleLabel,
)

from ...core.rules import (
    BUILTIN_DEFAULT_RULE_ID,
    active_rule_id,
    apply_rule_payload,
    current_rule_payload,
    default_rule_payload,
    rule_store,
    set_active_rule_id,
)
from ...i18n import tr
from ...signal_bus import signal_bus
from ..theme import (
    PAGE_MARGINS,
    PAGE_SPACING,
    FluentSplitter,
    apply_scrollbars,
    palette,
    set_secondary_text,
)
from ..fluent_dialogs import show_fluent_confirmation
from .rule_form import RuleFormWidget
from .rule_text import DRAFT_RULE_ID, _rule_display_name, _rule_summary


class RulesInterface(QWidget):
    """Manage named filter/download presets."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("RulesInterface")
        self._selected_id = ""
        self._draft_rule: dict[str, Any] | None = None
        self._rules_cache: list[dict[str, Any]] = []
        self._build_ui()
        self._reload_list(active_rule_id())
        signal_bus.active_rule_changed.connect(self._on_active_rule_changed)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(*PAGE_MARGINS)
        root.setSpacing(PAGE_SPACING)
        root.addWidget(TitleLabel(tr("Download Rules", "下载规则", "ダウンロードルール"), self))
        intro = BodyLabel(tr(
            "One rule combines filters, naming and download behavior. Selecting a rule on a download page applies it immediately.",
            "一个规则统一保存筛选、命名和下载行为；在下载页选择后会立即生效并成为默认规则。",
            "フィルター、命名、保存動作を一つにまとめ、選択時に即座に適用します。",
        ), self)
        intro.setWordWrap(True)
        set_secondary_text(intro)
        root.addWidget(intro)

        splitter = FluentSplitter(Qt.Orientation.Horizontal, self)
        self._splitter = splitter
        splitter.setChildrenCollapsible(False)
        root.addWidget(splitter, 1)

        splitter.addWidget(self._build_rule_list(splitter))
        splitter.addWidget(self._build_form_panel(splitter))
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([340, 1120])
        apply_scrollbars(self._list)

    def _build_rule_list(self, splitter) -> CardWidget:
        left = CardWidget(splitter)
        left.setMinimumWidth(280)
        left.setMaximumWidth(460)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(14, 14, 14, 14)
        left_layout.setSpacing(10)
        left_layout.addWidget(SubtitleLabel(tr("Rules", "规则列表", "ルール一覧"), left))
        hint = BodyLabel(tr("The dot marks the current default.", "带 ● 的是当前默认规则。", "● は現在の既定ルールです。"), left)
        hint.setWordWrap(True)
        set_secondary_text(hint)
        left_layout.addWidget(hint)
        self._list = ListWidget(left)
        self._list.setSpacing(4)
        self._list.setWordWrap(True)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setStyleSheet(self._list_style())
        self._list.currentItemChanged.connect(self._on_rule_selected)
        left_layout.addWidget(self._list, 1)
        actions = QGridLayout()
        actions.setHorizontalSpacing(8)
        self._new_btn = PrimaryPushButton(tr("New", "新建", "新規"), left, FluentIcon.ADD)
        self._duplicate_btn = PushButton(tr("Duplicate", "复制", "複製"), left, FluentIcon.COPY)
        self._delete_btn = PushButton(tr("Delete", "删除", "削除"), left, FluentIcon.DELETE)
        self._new_btn.clicked.connect(self._new_rule)
        self._duplicate_btn.clicked.connect(self._duplicate_rule)
        self._delete_btn.clicked.connect(self._delete_rule)
        actions.addWidget(self._new_btn, 0, 0)
        actions.addWidget(self._duplicate_btn, 0, 1)
        actions.addWidget(self._delete_btn, 0, 2)
        left_layout.addLayout(actions)
        return left

    def _build_form_panel(self, splitter) -> QWidget:
        right = QWidget(splitter)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(10)
        self._form_scroll = ScrollArea(right)
        self._form_scroll.setWidgetResizable(True)
        self._form_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._form_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._form = RuleFormWidget()
        self._form.setObjectName("RuleFormWidget")
        self._form.name_edit.textChanged.connect(self._on_form_name_changed)
        self._form_scroll.setWidget(self._form)
        # Scope transparency to the scroll area and form only: a bare
        # "background-color" rule cascades into every label and paints
        # mismatched patches inside the cards.
        self._form_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self._form_scroll.viewport().setStyleSheet("background: transparent;")
        self._form.setStyleSheet("QWidget#RuleFormWidget { background: transparent; }")
        right_layout.addWidget(self._form_scroll, 1)
        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        bottom.addStretch()
        self._apply_btn = PushButton(tr("Set as default", "设为当前默认", "既定に設定"), right, FluentIcon.ACCEPT)
        self._save_btn = PrimaryPushButton(tr("Save rule", "保存规则", "ルールを保存"), right, FluentIcon.SAVE)
        self._apply_btn.clicked.connect(self._apply_current)
        self._save_btn.clicked.connect(self._save_rule)
        bottom.addWidget(self._apply_btn)
        bottom.addWidget(self._save_btn)
        right_layout.addLayout(bottom)
        return right

    def _save_rule_if_enabled(self):
        if self._save_btn.isEnabled():
            self._save_rule()

    def _list_style(self) -> str:
        p = palette()
        return (
            "QListWidget{background:transparent;border:none;outline:none;}"
            f"QListWidget::item{{padding:10px;border:1px solid {p.border};border-radius:8px;color:{p.text};}}"
            f"QListWidget::item:hover{{border:1px solid {p.text_disabled};}}"
            f"QListWidget::item:selected{{border:1px solid {p.accent};color:{p.text};}}"
        )

    def _reload_list(self, select_id: str | None = None):
        saved_rules = rule_store.list_available()
        self._rules_cache = list(saved_rules)
        if self._draft_rule:
            self._rules_cache.insert(1 if self._rules_cache else 0, self._draft_rule)
        active_id = active_rule_id()
        self._list.blockSignals(True)
        self._list.clear()
        for rule in self._rules_cache:
            active_mark = "● " if rule["id"] == active_id else "  "
            if rule["id"] == DRAFT_RULE_ID:
                kind = tr("Unsaved draft", "未保存草稿", "未保存の下書き")
            else:
                kind = tr("Built-in default", "内置默认", "内蔵の既定") if rule.get("builtin") else tr("Saved rule", "已保存规则", "保存済み")
            item = QListWidgetItem(f"{active_mark}{_rule_display_name(rule)}\n{kind} · {_rule_summary(rule['payload'])}")
            item.setData(Qt.ItemDataRole.UserRole, rule["id"])
            item.setSizeHint(QSize(0, 72))
            item.setToolTip(str(rule["payload"].get("filename_template", "")))
            self._list.addItem(item)
        self._list.blockSignals(False)
        target = select_id or active_id
        for index in range(self._list.count()):
            if self._list.item(index).data(Qt.ItemDataRole.UserRole) == target:
                self._list.setCurrentRow(index)
                self._paint_list_items()
                return
        if self._list.count():
            self._list.setCurrentRow(0)
        self._paint_list_items()

    def _paint_list_items(self):
        p = palette()
        normal = QColor(0, 0, 0, 0)
        default_bg = QColor(p.success_bg)
        selected_bg = QColor(p.selected)
        draft_bg = QColor(p.warning_bg)
        selected_default_bg = QColor(p.selected)
        for index in range(self._list.count()):
            item = self._list.item(index)
            rule_id = str(item.data(Qt.ItemDataRole.UserRole) or "")
            is_selected = item is self._list.currentItem()
            if rule_id == DRAFT_RULE_ID:
                color = draft_bg
            elif rule_id == active_rule_id() and is_selected:
                color = selected_default_bg
            elif rule_id == active_rule_id():
                color = default_bg
            elif is_selected:
                color = selected_bg
            else:
                color = normal
            item.setBackground(QBrush(color))

    def _on_rule_selected(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None):
        if current is None:
            return
        rule_id = str(current.data(Qt.ItemDataRole.UserRole) or "")
        rule = next((r for r in self._rules_cache if r["id"] == rule_id), None)
        if not rule:
            return
        self._selected_id = rule_id
        builtin = bool(rule.get("builtin"))
        self._form.set_builtin_mode(builtin)
        name = tr("Default Download (built in)", "默认下载（内置）", "既定ダウンロード（内蔵）") if builtin else rule["name"]
        self._form.name_edit.setText(name)
        self._form.load_payload(rule["payload"])
        self._save_btn.setEnabled(not builtin)
        self._delete_btn.setEnabled(not builtin)
        self._paint_list_items()

    def _on_form_name_changed(self, name: str):
        if self._selected_id != DRAFT_RULE_ID or not self._draft_rule:
            return
        display_name = name.strip() or tr("New Rule", "新规则", "新規ルール")
        self._draft_rule["name"] = display_name
        for index in range(self._list.count()):
            item = self._list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == DRAFT_RULE_ID:
                item.setText(f"  {display_name}\n{tr('Unsaved draft', '未保存草稿', '未保存の下書き')} · {_rule_summary(self._draft_rule['payload'])}")
                break
        self._paint_list_items()

    def _new_rule(self):
        self._draft_rule = {
            "id": DRAFT_RULE_ID,
            "name": tr("New Rule", "新规则", "新規ルール"),
            "created_at": "",
            "updated_at": "",
            "builtin": False,
            "payload": current_rule_payload(),
        }
        self._selected_id = DRAFT_RULE_ID
        self._form.set_builtin_mode(False)
        self._form.name_edit.setText(self._draft_rule["name"])
        self._form.load_payload(self._draft_rule["payload"])
        self._save_btn.setEnabled(True)
        self._delete_btn.setEnabled(True)
        self._reload_list(DRAFT_RULE_ID)
        self._form.name_edit.setFocus()

    def _duplicate_rule(self):
        try:
            payload = self._form.payload()
        except ValueError as exc:
            self._show_error(str(exc))
            return
        base = (
            self._form.name_edit.text()
            .replace("（内置）", "")
            .replace("（内蔵）", "")
            .replace(" (built in)", "")
            .strip()
        )
        self._draft_rule = {
            "id": DRAFT_RULE_ID,
            "name": (base or tr("New Rule", "新规则", "新規ルール"))
            + tr(" copy", " 副本", " コピー"),
            "created_at": "",
            "updated_at": "",
            "builtin": False,
            "payload": payload,
        }
        self._selected_id = DRAFT_RULE_ID
        self._form.set_builtin_mode(False)
        self._form.name_edit.setText(self._draft_rule["name"])
        self._form.load_payload(payload)
        self._save_btn.setEnabled(True)
        self._delete_btn.setEnabled(True)
        self._reload_list(DRAFT_RULE_ID)

    def _save_rule(self):
        try:
            payload = self._form.payload()
            old_id = self._selected_id if self._selected_id != DRAFT_RULE_ID else ""
            rule = rule_store.save(self._form.name_edit.text(), payload, old_id or None)
        except ValueError as exc:
            self._show_error(str(exc))
            return
        self._draft_rule = None
        self._selected_id = rule["id"]
        if active_rule_id() == old_id:
            apply_rule_payload(rule["payload"])
            signal_bus.download_options_changed.emit()
            signal_bus.active_rule_changed.emit(rule["id"])
        signal_bus.rules_changed.emit()
        self._reload_list(rule["id"])
        InfoBar.success(title=tr("Rule saved", "规则已保存", "ルールを保存しました"), content=_rule_display_name(rule), orient=Qt.Orientation.Horizontal, isClosable=True, position=InfoBarPosition.TOP, duration=1800, parent=self)

    def _delete_rule(self):
        if self._selected_id == DRAFT_RULE_ID:
            self._draft_rule = None
            self._selected_id = active_rule_id()
            self._reload_list(self._selected_id)
            return
        if not self._selected_id or self._selected_id == BUILTIN_DEFAULT_RULE_ID:
            return
        if not show_fluent_confirmation(
            self,
            tr("Delete rule", "删除规则", "ルールを削除"),
            tr("Delete the selected rule?", "确定删除当前规则？", "選択したルールを削除しますか？"),
            yes_text=tr("Delete", "删除", "削除"),
            no_text=tr("Cancel", "取消", "キャンセル"),
        ):
            return
        deleted_active = active_rule_id() == self._selected_id
        if rule_store.delete(self._selected_id):
            self._selected_id = BUILTIN_DEFAULT_RULE_ID if deleted_active else ""
            if deleted_active:
                set_active_rule_id(BUILTIN_DEFAULT_RULE_ID)
                apply_rule_payload(default_rule_payload())
                signal_bus.active_rule_changed.emit(BUILTIN_DEFAULT_RULE_ID)
                signal_bus.download_options_changed.emit()
            signal_bus.rules_changed.emit()
            self._reload_list(self._selected_id or None)

    def _apply_current(self):
        if self._selected_id == DRAFT_RULE_ID:
            InfoBar.warning(title=tr("Save first", "请先保存规则", "先に保存してください"), content=tr("Save the new rule before making it default.", "新规则保存后才能设为默认。", "新規ルールは保存後に既定へ設定できます。"), orient=Qt.Orientation.Horizontal, isClosable=True, position=InfoBarPosition.TOP, duration=2200, parent=self)
            return
        if not self._selected_id:
            InfoBar.warning(title=tr("Save first", "请先保存规则", "先に保存してください"), content=tr("Save the new rule before making it default.", "新规则保存后才能设为默认。", "新規ルールは保存後に既定へ設定できます。"), orient=Qt.Orientation.Horizontal, isClosable=True, position=InfoBarPosition.TOP, duration=2200, parent=self)
            return
        try:
            payload = self._form.payload()
        except ValueError as exc:
            self._show_error(str(exc))
            return
        apply_rule_payload(payload)
        set_active_rule_id(self._selected_id)
        signal_bus.download_options_changed.emit()
        signal_bus.active_rule_changed.emit(self._selected_id)
        self._reload_list(self._selected_id)
        InfoBar.success(title=tr("Default rule updated", "默认规则已更新", "既定ルールを更新しました"), content=self._form.name_edit.text(), orient=Qt.Orientation.Horizontal, isClosable=True, position=InfoBarPosition.TOP, duration=1800, parent=self)

    def _on_active_rule_changed(self, rule_id: str):
        self._reload_list(self._selected_id or rule_id)

    def _show_error(self, content: str):
        InfoBar.error(title=tr("Invalid rule", "规则无效", "ルールが無効"), content=content, orient=Qt.Orientation.Horizontal, isClosable=True, position=InfoBarPosition.TOP, duration=2500, parent=self)

    def draft_state(self) -> dict[str, Any]:
        return {
            "selected": self._selected_id,
            "draft_rule": copy.deepcopy(self._draft_rule),
            "form": self._form.raw_state(),
        }

    def restore_draft(self, state: dict[str, Any]):
        draft_rule = state.get("draft_rule")
        self._draft_rule = copy.deepcopy(draft_rule) if isinstance(draft_rule, dict) else None
        selected = str(state.get("selected") or "")
        self._reload_list(selected or None)
        if selected and self._selected_id == selected and isinstance(state.get("form"), dict):
            self._form.load_raw_state(state["form"])

    def refresh_theme_styles(self):
        self._list.setStyleSheet(self._list_style())
        apply_scrollbars(self._list)
        self._reload_list(self._selected_id or None)
