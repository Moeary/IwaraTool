"""The Qt Multimedia objects behind the video player (replaceable in tests)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QEventLoop, QMetaObject, QObject, Qt, QUrl, Q_ARG
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


def release_source(player: QObject) -> None:
    """Drop the player's media, letting Qt join its decoder threads without the GIL.

    ``QMediaPlayer.setSource`` waits for the FFmpeg backend's threads to end,
    and called from Python it waits holding the GIL.  Those threads call back
    into PySide while they wind down (``QAudioOutput.disconnectNotify``, the
    clean-up of Python-owned objects), which needs the GIL: the GUI thread and
    the decoders then wait for each other forever, e.g. on quit or on leaving
    a post while its video plays.  Queued, the call runs from Qt's event loop,
    which has released the GIL; a local loop delivers it before returning.
    """

    if player.metaObject().indexOfMethod("setSource(QUrl)") < 0:
        player.setSource(QUrl())  # a test double without the Qt slot
        return
    loop = QEventLoop()
    queued = Qt.ConnectionType.QueuedConnection
    QMetaObject.invokeMethod(player, "setSource", queued, Q_ARG(QUrl, QUrl()))
    QMetaObject.invokeMethod(loop, "quit", queued)  # delivered right after setSource
    loop.exec(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
