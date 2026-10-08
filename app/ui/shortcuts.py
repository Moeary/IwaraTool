"""Turns the shortcut catalogue into live QShortcuts and keeps them current."""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import QWidget

from ..core import shortcut_defs
from ..logging_setup import get_logger
from ..signal_bus import signal_bus

logger = get_logger(__name__)

_PORTABLE = QKeySequence.SequenceFormat.PortableText


class ShortcutRegistry:
    """Owns every QShortcut so a settings change reaches all of them."""

    def __init__(self):
        self._bound: dict[str, list[QShortcut]] = {}

    def bind(self, widget: QWidget, handlers: dict[str, Callable[[], None]]) -> None:
        """Bind ``handlers`` to ``widget``.

        A shortcut whose parent widget is hidden never fires, so page-scoped
        actions are only live while their page is the visible one.
        """
        self._prune()
        for action_id, handler in handlers.items():
            shortcut = QShortcut(QKeySequence(shortcut_defs.key_for(action_id), _PORTABLE), widget)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.setAutoRepeat(False)
            shortcut.activated.connect(lambda handler=handler: handler())
            self._bound.setdefault(action_id, []).append(shortcut)

    @staticmethod
    def _alive(shortcut: QShortcut) -> bool:
        try:
            shortcut.parent()
        except RuntimeError:  # its widget was destroyed (e.g. language reload)
            return False
        return True

    def _prune(self) -> None:
        for action_id in list(self._bound):
            self._bound[action_id] = [s for s in self._bound[action_id] if self._alive(s)]

    def live(self, action_id: str) -> list[QShortcut]:
        """The still-valid shortcuts bound to action_id."""
        return [s for s in self._bound.get(action_id, []) if self._alive(s)]

    def refresh(self) -> None:
        self._prune()
        for action_id, shortcuts in self._bound.items():
            sequence = QKeySequence(shortcut_defs.key_for(action_id), _PORTABLE)
            for shortcut in shortcuts:
                shortcut.setKey(sequence)


registry = ShortcutRegistry()
signal_bus.shortcuts_changed.connect(registry.refresh)


def sequence_from_event(event: QKeyEvent) -> str:
    """Portable text of the key combination in ``event`` ("" for lone modifiers)."""
    key = event.key()
    if key in (
        Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt, Qt.Key.Key_Meta,
        Qt.Key.Key_AltGr, Qt.Key.Key_unknown,
    ):
        return ""
    return QKeySequence(event.keyCombination()).toString(_PORTABLE)


def action_for_event(scope: str, event: QKeyEvent) -> str:
    """Id of the ``scope`` action bound to the pressed keys, or ``""``."""
    pressed = shortcut_defs.normalize(sequence_from_event(event))
    if not pressed:
        return ""
    for action in shortcut_defs.all_actions():
        if action.scope == scope and shortcut_defs.key_for(action.id) == pressed:
            return action.id
    return ""
