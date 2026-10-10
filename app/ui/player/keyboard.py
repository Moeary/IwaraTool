"""Player shortcuts, including hold-to-speed-up."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from ..shortcuts import action_for_event
from .playback import HOLD_BOOST_RATE, SEEK_STEP_MS, VOLUME_STEP


class PlayerKeyboardMixin:
    """Player shortcuts, including hold-to-speed-up."""

    def _begin_boost(self):
        if self._boosted:
            return
        self._boosted = True
        self._rate_before_boost = float(self._player.playbackRate() or 1.0)
        self._player.setPlaybackRate(max(self._rate_before_boost, HOLD_BOOST_RATE))
        self._show_status(f"▶▶ {self._player.playbackRate():g}x")

    def _end_boost(self, *, restore: bool = True):
        self._hold_timer.stop()
        if not self._boosted:
            return
        self._boosted = False
        if restore:
            self._player.setPlaybackRate(self._rate_before_boost)
        self._hide_status()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is getattr(self, "_video", None):
            kind = event.type()
            if kind == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self.setFocus()
                self._hold_timer.start()
                return True
            if kind == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
                if self._boosted:
                    self._end_boost()
                elif self._hold_timer.isActive():
                    self._hold_timer.stop()
                    # Wait out a possible double click before treating it as a tap.
                    self._click_timer.start(QApplication.doubleClickInterval())
                return True
            if kind == QEvent.Type.MouseButtonDblClick and event.button() == Qt.MouseButton.LeftButton:
                self._hold_timer.stop()
                self._click_timer.stop()
                self.toggle_fullscreen()
                return True
            if kind == QEvent.Type.MouseMove and self.is_fullscreen():
                self._controls.show()
                self._video.unsetCursor()
                self._hide_controls_timer.start()
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event: QKeyEvent):
        action = action_for_event("player", event)
        if action == "player_seek_forward":
            if event.isAutoRepeat():
                self._begin_boost()  # holding the key plays at double speed
            else:
                self._forward_key = event.key()
        elif action == "player_play_pause":
            self.toggle_play()
        elif action == "player_seek_back":
            self.seek_by(-SEEK_STEP_MS)
        elif action == "player_volume_up":
            self.change_volume(VOLUME_STEP)
        elif action == "player_volume_down":
            self.change_volume(-VOLUME_STEP)
        elif action == "player_mute":
            self.toggle_mute()
        elif action == "player_fullscreen":
            self.toggle_fullscreen()
        elif action == "player_exit_fullscreen" and self.is_fullscreen():
            self.toggle_fullscreen()
        elif action == "player_speed_up":
            self.step_speed(+1)
        elif action == "player_speed_down":
            self.step_speed(-1)
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    def keyReleaseEvent(self, event: QKeyEvent):
        if self._forward_key is not None and event.key() == self._forward_key and not event.isAutoRepeat():
            if self._boosted:
                self._end_boost()
            else:
                self.seek_by(SEEK_STEP_MS)
            self._forward_key = None
            event.accept()
            return
        super().keyReleaseEvent(event)
