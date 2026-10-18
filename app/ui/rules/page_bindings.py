"""The Rules page card that sets which rule each page downloads with."""

from __future__ import annotations

from PySide6.QtWidgets import QGridLayout, QVBoxLayout, QWidget

from qfluentwidgets import BodyLabel, CardWidget, StrongBodyLabel

from ...core.rules import PAGE_RULE_SCOPES
from ...i18n import tr
from ..theme import set_secondary_text
from .rule_picker import RulePicker
from .rule_text import page_scope_name


class PageRuleBindings(CardWidget):
    """One picker per page; the same choice shows in that page's own picker."""

    _COLUMNS = 3

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)
        layout.addWidget(StrongBodyLabel(tr("Rule per page", "各页面使用的规则", "ページごとのルール"), self))
        hint = BodyLabel(tr(
            "Downloads started on a page use its rule. \"Follow default\" uses the rule marked ●.",
            "从哪个页面发起下载就用哪个页面的规则；“跟随默认”即使用带 ● 的默认规则。",
            "各ページから始めたダウンロードはそのページのルールを使います。「既定に従う」は ● のルールです。",
        ), self)
        hint.setWordWrap(True)
        set_secondary_text(hint)
        layout.addWidget(hint)
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        self.pickers: dict[str, RulePicker] = {}
        for index, scope in enumerate(PAGE_RULE_SCOPES):
            row, column = divmod(index, self._COLUMNS)
            label = BodyLabel(page_scope_name(scope), self)
            picker = RulePicker(self, scope=scope)
            picker.combo.setMinimumWidth(170)
            grid.addWidget(label, row, column * 2)
            grid.addWidget(picker, row, column * 2 + 1)
            grid.setColumnStretch(column * 2 + 1, 1)
            self.pickers[scope] = picker
        layout.addLayout(grid)
