"""The "Keyboard Shortcuts" settings page: one card per area, click a key to rebind it."""
from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import (
    BodyLabel,
    FluentIcon,
    InfoBar,
    InfoBarPosition,
    PushButton,
    SubtitleLabel,
    ToolButton,
)

from ..core import shortcut_defs
from ..i18n import tr
from ..signal_bus import signal_bus
from .shortcuts import sequence_from_event

CARD_PREFIX = "shortcuts_"
GENERAL_CARD = "shortcuts_general"
# Display order of the per-area cards.
SCOPE_ORDER = (
    shortcut_defs.SCOPE_GLOBAL,
    shortcut_defs.SCOPE_DOWNLOAD,
    shortcut_defs.SCOPE_SEARCH,
    shortcut_defs.SCOPE_SUBSCRIPTIONS,
    shortcut_defs.SCOPE_HISTORY,
    shortcut_defs.SCOPE_REPAIR,
    shortcut_defs.SCOPE_RULES,
    shortcut_defs.SCOPE_SETTINGS,
    shortcut_defs.SCOPE_PLAYER,
)


def shortcut_card_keys() -> tuple[str, ...]:
    return (GENERAL_CARD, *(CARD_PREFIX + scope for scope in SCOPE_ORDER))


class ShortcutCaptureButton(PushButton):
    """Shows a key combination; click, then press the new one (Esc cancels, Backspace clears)."""

    sequenceChosen = Signal(str)  # portable text, "" = unassigned

    def __init__(self, parent: QWidget | None = None):
        self._capturing = False  # events can arrive while the base class is still initialising
        self._sequence = ""
        super().__init__(parent)
        self.setMinimumWidth(168)
        self.clicked.connect(self._start_capture)

    def set_sequence(self, portable: str) -> None:
        self._sequence = portable
        self._refresh_text()

    def _refresh_text(self) -> None:
        if self._capturing:
            self.setText(tr("Press the new keys…", "请按下新的快捷键…", "新しいキーを押してください…"))
        elif self._sequence:
            from PySide6.QtGui import QKeySequence

            self.setText(
                QKeySequence(self._sequence, QKeySequence.SequenceFormat.PortableText).toString(
                    QKeySequence.SequenceFormat.NativeText
                )
            )
        else:
            self.setText(tr("Unassigned", "未设置", "未設定"))

    def _start_capture(self) -> None:
        self._capturing = True
        self._refresh_text()
        self.setFocus()

    def _finish_capture(self) -> None:
        self._capturing = False
        self._refresh_text()

    def event(self, event: QEvent) -> bool:
        # Without this a bound shortcut (e.g. Ctrl+S) would fire instead of being recorded.
        if self._capturing and event.type() == QEvent.Type.ShortcutOverride:
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if not self._capturing:
            super().keyPressEvent(event)
            return
        key = event.key()
        if key == Qt.Key.Key_Escape and not event.modifiers():
            self._finish_capture()
        elif key in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete) and not event.modifiers():
            self._finish_capture()
            self.sequenceChosen.emit("")
        else:
            sequence = sequence_from_event(event)
            if not sequence:
                return  # only a modifier so far; keep waiting for the real key
            self._finish_capture()
            self.sequenceChosen.emit(sequence)
        event.accept()

    def focusOutEvent(self, event) -> None:
        if self._capturing:
            self._finish_capture()
        super().focusOutEvent(event)


class ShortcutSettingsCards:
    """Builds the shortcut cards into a ``SettingsSections`` board."""

    def __init__(self, board, owner: QWidget):
        self._owner = owner
        self._buttons: dict[str, ShortcutCaptureButton] = {}
        self._build_general_card(board)
        titles = shortcut_defs.scope_titles()
        for scope in SCOPE_ORDER:
            self._build_scope_card(board, scope, titles[scope])
        self.load()

    def _build_general_card(self, board):
        card = board.create_card(GENERAL_CARD)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)
        layout.addWidget(SubtitleLabel(tr("Keyboard Shortcuts", "键盘快捷键", "キーボードショートカット"), card))
        layout.addWidget(
            BodyLabel(
                tr(
                    "Click a key to record a new one: Esc cancels, Backspace clears it. A shortcut only works in its own area unless it is under \"Anywhere in the app\"; the same key may be reused across different pages.",
                    "点击按键后按下新的组合键即可修改：Esc 取消，Backspace 清除。快捷键只在所属页面生效（“全局”分组除外），不同页面之间可以使用相同按键。",
                    "キーをクリックして新しい組み合わせを押すと変更できます（Esc で取消、Backspace で解除）。「グローバル」以外は各ページ内でのみ有効で、ページが違えば同じキーを使えます。",
                ),
                card,
            )
        )
        reset_all = PushButton(tr("Reset all to defaults", "全部恢复默认", "すべて初期値に戻す"), card, FluentIcon.SYNC)
        reset_all.clicked.connect(self._reset_all)
        layout.addWidget(reset_all, alignment=Qt.AlignmentFlag.AlignLeft)
        board.add_card(GENERAL_CARD, card)

    def _build_scope_card(self, board, scope: str, title: str):
        key = CARD_PREFIX + scope
        card = board.create_card(key)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(8)
        layout.addWidget(SubtitleLabel(title, card))
        for action in shortcut_defs.all_actions():
            if action.scope != scope:
                continue
            row = QHBoxLayout()
            row.addWidget(BodyLabel(action.label, card))
            row.addStretch()
            button = ShortcutCaptureButton(card)
            button.sequenceChosen.connect(lambda seq, action_id=action.id: self._on_chosen(action_id, seq))
            reset = ToolButton(FluentIcon.SYNC, card)
            reset.setToolTip(tr("Restore the default", "恢复默认", "初期値に戻す"))
            reset.clicked.connect(lambda _checked=False, action_id=action.id: self._on_reset(action_id))
            row.addWidget(button)
            row.addWidget(reset)
            layout.addLayout(row)
            self._buttons[action.id] = button
        board.add_card(key, card)

    # ── State ────────────────────────────────────────────────────────────────

    def load(self) -> None:
        for action_id, button in self._buttons.items():
            button.set_sequence(shortcut_defs.key_for(action_id))

    def _changed(self) -> None:
        self.load()
        signal_bus.shortcuts_changed.emit()

    def _on_chosen(self, action_id: str, sequence: str) -> None:
        conflict = shortcut_defs.set_key(action_id, sequence)
        if conflict is not None:
            self._warn_conflict(conflict)
            self.load()
            return
        self._changed()

    def _on_reset(self, action_id: str) -> None:
        conflict = shortcut_defs.reset_key(action_id)
        if conflict is not None:
            self._warn_conflict(conflict)
            return
        self._changed()

    def _reset_all(self) -> None:
        shortcut_defs.reset_all()
        self._changed()

    def _warn_conflict(self, other: shortcut_defs.ShortcutAction) -> None:
        titles = shortcut_defs.scope_titles()
        InfoBar.warning(
            title=tr("Shortcut already in use", "快捷键已被占用", "ショートカットは使用中です"),
            content=tr(
                f"“{other.label}” ({titles.get(other.scope, other.scope)}) uses it. Unassign or change that one first.",
                f"“{other.label}”（{titles.get(other.scope, other.scope)}）正在使用。请先清除或修改它。",
                f"「{other.label}」（{titles.get(other.scope, other.scope)}）が使用しています。先にそちらを解除・変更してください。",
            ),
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=5000,
            parent=self._owner.window(),
        )

