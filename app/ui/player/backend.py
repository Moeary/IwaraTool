"""The Qt Multimedia objects behind the video player (replaceable in tests)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PySide6.QtWidgets import QWidget

from ...qt_runtime import ensure_multimedia_plugins


@dataclass
class Backend:
    """The Qt Multimedia objects the player drives (replaceable in tests)."""

    player: Any
    audio: Any
    video_widget: QWidget


def create_backend(parent: QWidget) -> Backend:
    ensure_multimedia_plugins()
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PySide6.QtMultimediaWidgets import QVideoWidget

    player = QMediaPlayer(parent)
    audio = QAudioOutput(parent)
    video = QVideoWidget(parent)
    player.setAudioOutput(audio)
    player.setVideoOutput(video)
    return Backend(player, audio, video)
