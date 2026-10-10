"""True fullscreen for the player, lifted out of its page and put back."""

from __future__ import annotations

from PySide6.QtCore import QRect, Qt
from PySide6.QtWidgets import QApplication

from qfluentwidgets import FluentIcon

from ...i18n import tr


class PlayerFullscreenMixin:
    """True fullscreen for the player, lifted out of its page and put back."""

    def is_fullscreen(self) -> bool:
        return self._home is not None

    def toggle_fullscreen(self):
        if self.is_fullscreen():
            self._leave_fullscreen()
        else:
            self._enter_fullscreen()

    def _enter_fullscreen(self):
        """Lift the player out of its page into a window covering the whole screen."""

        home = self.parentWidget()
        if home is None:
            return
        screen = self.window().screen() or QApplication.primaryScreen()
        layout = home.layout()
        index = layout.indexOf(self) if layout is not None else -1
        if layout is not None:
            layout.removeWidget(self)
        self._home = (home, index)
        self.setParent(None)
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setWindowTitle(self._title or tr("Video preview", "视频预览", "動画プレビュー"))
        if screen is not None:
            self.setGeometry(QRect(screen.geometry()))  # on the screen the page is on
        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        self.setFocus()
        self._fullscreen_btn.setIcon(FluentIcon.BACK_TO_WINDOW)
        self._hide_controls_timer.start()
        self._place_overlay()
        self.fullscreen_changed.emit(True)

    def _leave_fullscreen(self):
        home, index = self._home
        self._home = None
        self._hide_controls_timer.stop()
        self.hide()
        self.setWindowFlags(Qt.WindowType.Widget)
        self.setParent(home)
        layout = home.layout()
        if layout is not None:
            if index >= 0 and hasattr(layout, "insertWidget"):
                layout.insertWidget(index, self, 1)
            else:
                layout.addWidget(self)
        self._controls.show()
        self._video.unsetCursor()
        self._fullscreen_btn.setIcon(FluentIcon.FULL_SCREEN)
        self.show()
        top = home.window()
        top.raise_()
        top.activateWindow()
        self.setFocus()
        self._place_overlay()
        self.fullscreen_changed.emit(False)

    def _request_popout(self):
        if self.is_fullscreen():
            self._leave_fullscreen()
        self.popout_requested.emit()

    def _hide_controls_if_fullscreen(self):
        if self.is_fullscreen():
            self._controls.hide()
            self._video.setCursor(Qt.CursorShape.BlankCursor)
