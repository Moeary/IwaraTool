"""Stand-alone preview window around the video player, and keeping a window's normal geometry."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QEvent, QObject, QRect, Qt, QTimer, Signal
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

from qfluentwidgets import FluentWidget

from ...i18n import tr
from .backend import Backend, create_backend
from .video_player import VideoPlayer


class VideoPreviewWindow(FluentWidget):
    """A Fluent window (themed title bar, Mica) around a ``VideoPlayer``.

    Reused while it is open, but not after it was closed: closing destroys the
    native window, and the one Qt recreates on the next ``show()`` lacks the
    frameless-window styles, so Windows would maximize it under a left taskbar
    and leave a gap on the right.  ``closed`` tells the owner to drop it.
    """

    closed = Signal()

    def __init__(self, backend_factory: Callable[[QWidget], Backend] = create_backend, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(tr("Video preview", "视频预览", "動画プレビュー"))
        self.setWindowIcon(QApplication.windowIcon())
        self.resize(1000, 660)
        self.setMinimumSize(560, 400)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, self.titleBar.height(), 0, 0)  # leave room for the title bar
        root.setSpacing(0)
        self.player = VideoPlayer(backend_factory, self)
        self.player.title_changed.connect(self._on_title_changed)
        root.addWidget(self.player, 1)
        self.setFocusProxy(self.player)
        keep_normal_geometry(self)

    def _on_title_changed(self, title: str):
        self.setWindowTitle(title or tr("Video preview", "视频预览", "動画プレビュー"))

    def play_local(self, path: str, title: str = "", video_id: str = "", *, start_ms: int = 0):
        self.player.play_local(path, title, video_id, start_ms=start_ms)

    def play_remote(self, video_id: str, title: str = "", *, start_ms: int = 0):
        self.player.play_remote(video_id, title, start_ms=start_ms)

    def closeEvent(self, event):
        self.player.shutdown()
        super().closeEvent(event)
        if event.isAccepted():
            self.closed.emit()


class _NormalGeometryKeeper(QObject):
    """Puts a window back exactly where it was after maximize or fullscreen.

    With the taskbar on the left, Qt 6.6 restores a window shifted right by
    the taskbar's width (Windows keeps the restore position in work-area
    coordinates), so every maximize/restore cycle walks it off the screen.
    """

    def __init__(self, window: QWidget):
        super().__init__(window)
        self._window = window
        self._normal: QRect | None = None
        self._restoring = False  # the shifted position arriving now is not the user's
        window.installEventFilter(self)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is self._window:
            kind = event.type()
            if kind in (QEvent.Type.Move, QEvent.Type.Resize, QEvent.Type.Show):
                if self._is_normal() and not self._restoring:
                    self._normal = QRect(self._window.geometry())
            elif kind == QEvent.Type.WindowStateChange:
                was_normal = not event.oldState() & (
                    Qt.WindowState.WindowMaximized | Qt.WindowState.WindowFullScreen | Qt.WindowState.WindowMinimized
                )
                if self._is_normal() and not was_normal and self._normal is not None:
                    self._restoring = True
                    QTimer.singleShot(0, self._restore)
        return False

    def _is_normal(self) -> bool:
        state = self._window.windowState()
        return not state & (Qt.WindowState.WindowMaximized | Qt.WindowState.WindowFullScreen | Qt.WindowState.WindowMinimized)

    def _restore(self):
        saved = self._normal
        try:
            if saved is not None and self._is_normal() and self._window.geometry() != saved:
                self._window.setGeometry(saved)
        finally:
            self._restoring = False


def keep_normal_geometry(window: QWidget) -> None:
    """Install the restore-position fix on a top-level window (Windows only)."""

    import sys

    if sys.platform == "win32":
        _NormalGeometryKeeper(window)
