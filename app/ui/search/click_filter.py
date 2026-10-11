"""Route plain clicks on search results to the configured click actions."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QItemSelectionModel, QObject, Qt
from PySide6.QtWidgets import QTableWidget

from ..click_dispatch import ClickDispatcher


class _ResultClickFilter(QObject):
    """Plain clicks on the result grid / table go to the Settings click actions.

    Ctrl / Shift clicks, rubber-band selection on empty space and the context
    menu keep Qt's own behaviour; a plain press on a result is taken over so it
    toggles that result instead of replacing the whole selection.
    """

    def __init__(self, view, dispatcher: ClickDispatcher):
        super().__init__(view)
        self._view = view
        self._dispatcher = dispatcher
        self._pressed = None
        self._swallow_release = False
        view.installEventFilter(self)
        view.viewport().installEventFilter(self)

    def _target(self, event):
        item = self._view.itemAt(event.position().toPoint())
        if item is None:
            return None
        if isinstance(self._view, QTableWidget):
            return self._view.item(item.row(), 0)  # one stable object per row
        return item

    def eventFilter(self, obj, event):
        kind = event.type()
        # Cocoa does not activate QListWidget / QTableWidget items on Return.
        if obj is self._view and kind == QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                item = self._view.currentItem()
                if item is not None:
                    self._view.itemActivated.emit(item)
                return True
            return False
        if obj is self._view:
            return False
        if kind not in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease, QEvent.Type.MouseButtonDblClick):
            return False
        if event.button() != Qt.MouseButton.LeftButton:
            return False
        plain = not event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
        if kind == QEvent.Type.MouseButtonRelease:
            if self._swallow_release:
                self._swallow_release = False
                return True
            target, self._pressed = self._pressed, None
            if target is not None and plain and target is self._target(event):
                self._dispatcher.click(target)
                return True
            return False
        target = self._target(event) if plain else None
        if target is None:
            self._pressed = None
            return False
        self._view.setFocus(Qt.FocusReason.MouseFocusReason)
        index = self._view.indexFromItem(target)
        self._view.selectionModel().setCurrentIndex(index, QItemSelectionModel.SelectionFlag.NoUpdate)
        if kind == QEvent.Type.MouseButtonPress:
            self._pressed = target
        else:
            self._pressed = None
            self._swallow_release = True
            self._dispatcher.double_click(target)
        return True
