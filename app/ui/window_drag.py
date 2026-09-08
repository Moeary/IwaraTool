"""Keep Windows title-bar dragging inside one native move operation."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt


class WindowsTitleBarDragFilter(QObject):
    """Replace the dependency's mouse-move-driven Windows drag handler.

    Starting a native move from every delivered mouse move can re-enter the
    Windows move loop, including after release. Start on a left press instead;
    Qt then owns capture, screen coordinates and the end of the operation.
    """

    def __init__(self, title_bar):
        super().__init__(title_bar)
        self._title_bar = title_bar
        title_bar.installEventFilter(self)

    def eventFilter(self, watched, event):
        if watched is not self._title_bar:
            return False
        if event.type() == QEvent.Type.MouseMove:
            # Never let a queued move start a second native drag. Child button
            # events are unaffected: this filter is installed on the bar only.
            return True
        if event.type() != QEvent.Type.MouseButtonPress:
            return False
        if event.button() != Qt.MouseButton.LeftButton:
            return False
        if not self._title_bar.canDrag(event.position().toPoint()):
            return False
        window = self._title_bar.window()
        handle = window.windowHandle()
        if handle is None or not handle.startSystemMove():
            # Compatibility fallback, still issued only once per press.
            from qframelesswindow.utils import startSystemMove

            startSystemMove(window, event.globalPosition().toPoint())
        event.accept()
        return True
