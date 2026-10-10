"""How many videos wait for confirmation, as a badge on the download workbench's sidebar item."""

from __future__ import annotations

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QWidget

from qfluentwidgets import InfoBadge, InfoBadgePosition

from ...core.manager import download_manager
from ...signal_bus import signal_bus


class StagingBadge(QObject):
    """Shows the waiting count next to "Download Hub"; hidden at zero."""

    def __init__(self, navigation: QWidget, item: QWidget):
        super().__init__(navigation)
        self._navigation = navigation
        self._item = item
        self._badge: InfoBadge | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self.refresh)
        signal_bus.staging_changed.connect(self._timer.start)
        self.refresh()

    def refresh(self):
        count = int(download_manager.staged_counts().get("total", 0))
        if count <= 0:
            if self._badge is not None:
                self._badge.hide()
            return
        text = "99+" if count > 99 else str(count)
        if self._badge is None:
            self._badge = InfoBadge.attension(
                text, parent=self._navigation, target=self._item, position=InfoBadgePosition.NAVIGATION_ITEM,
            )
        else:
            self._badge.setText(text)
            self._badge.adjustSize()
        self._badge.show()
