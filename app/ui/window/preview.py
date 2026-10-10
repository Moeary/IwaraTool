"""The pop-out video preview window."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt

from qfluentwidgets import InfoBar, InfoBarPosition

from ...i18n import tr
from ...core import video_player
from ..player.preview_window import VideoPreviewWindow


class WindowPreviewMixin:
    """The pop-out video preview window."""

    def _on_video_preview_requested(self, video_id: str, title: str, path: str):
        """Play locally when the file is on disk, otherwise stream it in a window."""
        video_id = str(video_id or "").strip()
        local = path if path and os.path.isfile(path) else video_player.local_video_path(video_id)
        if local and video_player.player_mode() != video_player.MODE_BUILTIN:
            ok, message = video_player.open_local_video(local)
            if not ok:
                InfoBar.warning(
                    title=tr("Cannot open", "无法打开", "開けません"),
                    content=message,
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=4000,
                    parent=self,
                )
            return
        if not local and not video_id:
            return
        self._show_preview_window(video_id, title, local)

    def _on_video_popout_requested(self, video_id: str, title: str, position: int):
        """An embedded player asked for the window: always the built-in one, at the same spot."""
        video_id = str(video_id or "").strip()
        if video_id:
            self._show_preview_window(video_id, title, video_player.local_video_path(video_id), start_ms=position)

    def _show_preview_window(self, video_id: str, title: str, local: str, *, start_ms: int = 0):
        if self._preview_window is None:
            self._preview_window = VideoPreviewWindow()
            self._preview_window.closed.connect(self._on_preview_window_closed)
        window = self._preview_window
        if local:
            window.play_local(local, title, video_id, start_ms=start_ms)
        else:
            window.play_remote(video_id, title, start_ms=start_ms)
        window.show()
        window.raise_()
        window.activateWindow()
        window.setFocus()

    def _on_preview_window_closed(self):
        """A closed player window is not shown again; the next video gets a fresh one."""
        window = self.sender()
        if window is not None and window is self._preview_window:
            self._preview_window = None
            window.deleteLater()

    def _close_preview_window(self):
        window, self._preview_window = self._preview_window, None
        if window is not None:
            window.close()
            window.deleteLater()
