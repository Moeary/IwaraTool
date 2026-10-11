"""Follow the media player: position, duration, state, status, errors and seeking."""

from __future__ import annotations

from qfluentwidgets import FluentIcon

from ...i18n import tr
from .playback import format_time


class PlayerMediaEventsMixin:
    """Follow the media player: position, duration, state, status, errors and seeking."""

    def _on_position(self, position: int):
        self._time_label.setText(format_time(position))
        if not self._seeking:
            self._seek.setValue(int(position))

    def _on_duration(self, duration: int):
        self._seek.setRange(0, max(0, int(duration)))
        self._duration_label.setText(format_time(duration))

    def _on_state(self, _state):
        self._play_btn.setIcon(FluentIcon.PAUSE if self._is_playing() else FluentIcon.PLAY)

    def _on_media_status(self, status):
        from PySide6.QtMultimedia import QMediaPlayer

        statuses = QMediaPlayer.MediaStatus
        if status in (statuses.LoadingMedia, statuses.StalledMedia, statuses.BufferingMedia):
            self._show_status(tr("Buffering…", "缓冲中…", "バッファリング中…"))
        elif status in (statuses.LoadedMedia, statuses.BufferedMedia, statuses.EndOfMedia):
            if status == statuses.LoadedMedia and self._pending_start_ms:
                start, self._pending_start_ms = self._pending_start_ms, 0
                self._player.setPosition(start)  # continue where the other player was
            if not self._boosted and not self._pending_error:
                self._hide_status()
        elif status == statuses.InvalidMedia and not self._pending_error:
            self._on_error(None, tr("The media format is not supported", "不支持该媒体格式", "このメディア形式は未対応です"))

    def _on_error(self, _error, message: str = ""):
        text = message or self._player.errorString()
        if not text:
            return
        self._pending_error = text
        hint = ""
        if self._remote:
            hint = tr(
                " Use the globe button to open the page in your browser.",
                " 可点击地球按钮在浏览器中打开页面。",
                " 地球ボタンでブラウザーを開けます。",
            )
        self._show_status(
            tr(f"Playback error: {text}.{hint}", f"播放出错：{text}。{hint}", f"再生エラー：{text}。{hint}")
        )

    def _on_seek_pressed(self):
        self._seeking = True

    def _on_seek_moved(self, value: int):
        self._time_label.setText(format_time(value))

    def _on_seek_released(self):
        self._player.setPosition(int(self._seek.value()))
        self._seeking = False

    def _on_seek_clicked(self, value: int):
        self._player.setPosition(int(value))

    def _is_playing(self) -> bool:
        from PySide6.QtMultimedia import QMediaPlayer

        return self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def _at_end(self) -> bool:
        duration = int(self._player.duration() or 0)
        return duration > 0 and int(self._player.position() or 0) >= duration - 200
