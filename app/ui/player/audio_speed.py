"""Playback speed and volume of the player, with the volume remembered."""

from __future__ import annotations

from qfluentwidgets import FluentIcon

from ...config import app_config
from .playback import SPEEDS, nearest_speed_index


class PlayerAudioSpeedMixin:
    """Playback speed and volume of the player, with the volume remembered."""

    def set_speed(self, rate: float):
        self._speed.setCurrentIndex(nearest_speed_index(rate))  # triggers _on_speed_changed

    def step_speed(self, direction: int):
        index = max(0, min(len(SPEEDS) - 1, nearest_speed_index(self._player.playbackRate()) + direction))
        self._speed.setCurrentIndex(index)

    def change_volume(self, delta: float):
        self._volume.setValue(int(round(max(0.0, min(1.0, self._audio.volume() + delta)) * 100)))

    def toggle_mute(self):
        self._audio.setMuted(not self._audio.isMuted())
        self._refresh_volume_icon()

    def _on_speed_changed(self, index: int):
        if 0 <= index < len(SPEEDS):
            self._set_rate(SPEEDS[index])

    def _set_rate(self, rate: float):
        self._player.setPlaybackRate(rate)
        index = nearest_speed_index(rate)
        if self._speed.currentIndex() != index:
            blocked = self._speed.blockSignals(True)
            self._speed.setCurrentIndex(index)
            self._speed.blockSignals(blocked)

    def _on_volume_changed(self, value: int):
        self._audio.setVolume(max(0, min(100, int(value))) / 100.0)
        if value > 0 and self._audio.isMuted():
            self._audio.setMuted(False)
        self._refresh_volume_icon()

    def _refresh_volume_icon(self):
        muted = self._audio.isMuted() or self._audio.volume() <= 0
        self._mute_btn.setIcon(FluentIcon.MUTE if muted else FluentIcon.VOLUME)

    @staticmethod
    def _saved_volume() -> float:
        try:
            return max(0.0, min(1.0, float(app_config.get_ui_value("preview_volume_v1", 0.8))))
        except (TypeError, ValueError):
            return 0.8

    def _save_volume(self):
        app_config.set_ui_value("preview_volume_v1", round(float(self._audio.volume()), 2))
