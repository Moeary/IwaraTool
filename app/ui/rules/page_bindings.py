"""The Rules page card that sets which rule each page downloads with."""

from __future__ import annotations

from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import BodyLabel, CaptionLabel, CardWidget, StrongBodyLabel

from ...core.rules import PAGE_RULE_SCOPES
from ...presentation import rule_editing
from ..style.theme import set_secondary_text
from .rule_picker import RulePicker
from ...presentation.rules import page_scope_name


class PageRuleBindings(CardWidget):
    """One picker per page, side by side; the same choice shows in that page's own picker.

    It sits above the rule list and editor on purpose: it assigns rules to
    pages and does not follow the rule being edited below.
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 14)
        layout.setSpacing(8)
        header = QHBoxLayout()
        header.setSpacing(12)
        header.addWidget(StrongBodyLabel(rule_editing.page_rules_title(), self))
        hint = BodyLabel(rule_editing.page_rules_hint(), self)
        hint.setWordWrap(True)
        set_secondary_text(hint)
        header.addWidget(hint, 1)
        layout.addLayout(header)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(4)
        self.pickers: dict[str, RulePicker] = {}
        for column, scope in enumerate(PAGE_RULE_SCOPES):
            label = CaptionLabel(page_scope_name(scope), self)
            set_secondary_text(label)
            picker = RulePicker(self, scope=scope)
            picker.combo.setMinimumWidth(150)
            grid.addWidget(label, 0, column)
            grid.addWidget(picker, 1, column)
            grid.setColumnStretch(column, 1)
            self.pickers[scope] = picker
        layout.addLayout(grid)
