"""Tell scrolling the user asked for apart from scrolling the code did."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QAbstractScrollArea, QWidget


_USER_EVENTS = frozenset(
    (
        QEvent.Type.Wheel,
        QEvent.Type.MouseButtonPress,
        QEvent.Type.KeyPress,
    )
)


class UserScrollWatcher(QObject):
    """Counts wheel, scroll-bar and key input on a scroll area.

    A view that rebuilds itself in slices remembers ``serial`` when it starts
    and only puts the old scroll position back if the number has not moved,
    so a rebuild never drags the page away from where the user scrolled to.
    """

    def __init__(self, area: QAbstractScrollArea):
        super().__init__(area)
        self.serial = 0
        watched = [area, area.viewport(), area.verticalScrollBar()]
        delegate = getattr(area, "scrollDelagate", None)  # qfluentwidgets' overlay bar (sic)
        overlay = getattr(delegate, "vScrollBar", None)
        if overlay is not None:
            watched.append(overlay)
            watched.extend(overlay.findChildren(QWidget))  # its handle and groove
        for widget in watched:
            widget.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() in _USER_EVENTS:
            self.serial += 1
        return False
