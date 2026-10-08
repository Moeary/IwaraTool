"""F1 cheat sheet: every shortcut with the key it currently has."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import BodyLabel, CaptionLabel, MessageBoxBase, PushButton, SubtitleLabel

from ..core import shortcut_defs
from ..i18n import tr
from .media_card import transparent_scroll_area
from .theme import palette, set_secondary_text


def grouped_shortcuts(current_scope: str = "") -> list[tuple[str, list[tuple[str, str]]]]:
    """``[(scope title, [(label, key), ...]), ...]``: global first, then the page being used.

    Unassigned actions are left out; they cannot be pressed.
    """

    titles = shortcut_defs.scope_titles()
    by_scope: dict[str, list[tuple[str, str]]] = {}
    for action in shortcut_defs.all_actions():
        key = shortcut_defs.key_for(action.id)
        if key:
            by_scope.setdefault(action.scope, []).append((action.label, key))
    order = [shortcut_defs.SCOPE_GLOBAL]
    if current_scope and current_scope != shortcut_defs.SCOPE_GLOBAL:
        order.append(current_scope)
    order += [scope for scope in titles if scope not in order]
    return [(titles[scope], by_scope[scope]) for scope in order if by_scope.get(scope)]


class ShortcutHelpDialog(MessageBoxBase):
    """Read-only list; the "Customize" button jumps to Settings → Keyboard Shortcuts."""

    def __init__(self, parent: QWidget, current_scope: str = ""):
        super().__init__(parent)
        self.customize_requested = False
        self.viewLayout.addWidget(SubtitleLabel(tr("Keyboard shortcuts", "键盘快捷键", "キーボードショートカット"), self))
        hint = CaptionLabel(
            tr(
                "The shortcuts of the page you are on are listed right after the global ones.",
                "当前所在页面的快捷键紧跟在全局快捷键之后。",
                "現在のページのショートカットは全体用の直後に表示されます。",
            ),
            self,
        )
        set_secondary_text(hint)
        self.viewLayout.addWidget(hint)

        scroll = transparent_scroll_area("ShortcutHelpScroll", self)
        body = QWidget()
        column = QVBoxLayout(body)
        column.setContentsMargins(0, 4, 8, 4)
        column.setSpacing(4)
        for title, rows in grouped_shortcuts(current_scope):
            heading = BodyLabel(title, body)
            font = heading.font()
            font.setBold(True)
            heading.setFont(font)
            column.addSpacing(8)
            column.addWidget(heading)
            for label, key in rows:
                row = QHBoxLayout()
                row.setSpacing(12)
                name = BodyLabel(label, body)
                chip = BodyLabel(key, body)
                chip.setStyleSheet(
                    f"color: {palette().accent}; font-family: 'Cascadia Mono', 'Consolas', monospace;"
                )
                chip.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                row.addWidget(name, 1)
                row.addWidget(chip)
                column.addLayout(row)
        column.addStretch(1)
        scroll.setWidget(body)
        self.viewLayout.addWidget(scroll, 1)

        self.customize_btn = PushButton(tr("Change shortcuts…", "修改快捷键…", "ショートカットを変更…"), self)
        self.customize_btn.clicked.connect(self._customize)
        self.viewLayout.addWidget(self.customize_btn, 0, Qt.AlignmentFlag.AlignLeft)

        self.yesButton.setText(tr("Close", "关闭", "閉じる"))
        self.cancelButton.hide()
        self.widget.setMinimumSize(560, 560)

    def _customize(self):
        self.customize_requested = True
        self.accept()
