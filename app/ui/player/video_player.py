"""Built-in video player: streams un-downloaded videos or plays local files.

``VideoPlayer`` is the player itself, a plain widget the detail page embeds;
``VideoPreviewWindow`` puts one in a separate window for the places that ask
for a window explicitly.

Controls: play/pause, seek, speed, volume, fullscreen. Holding the left mouse
button on the picture (or the Right arrow key) plays at double speed until it
is released.  Fullscreen lifts the player out of its page into a borderless
window covering the whole screen (taskbar included), and puts it back after.
"""
from __future__ import annotations

import threading
import webbrowser
from typing import Callable

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtWidgets import QWidget

from ...core import preview_stream
from ...i18n import tr
from ...logging_setup import get_logger
from .backend import Backend, create_backend
from .playback import CONTROLS_HIDE_MS, HOLD_BOOST_MS
from .audio_speed import PlayerAudioSpeedMixin
from .fullscreen import PlayerFullscreenMixin
from .keyboard import PlayerKeyboardMixin
from .media_events import PlayerMediaEventsMixin
from .player_layout import PlayerLayoutMixin

logger = get_logger(__name__)


class VideoPlayer(
    PlayerLayoutMixin,
    PlayerFullscreenMixin,
    PlayerKeyboardMixin,
    PlayerMediaEventsMixin,
    PlayerAudioSpeedMixin,
    QWidget,
):
    """A black video surface with a control bar, embeddable in any layout."""

    _stream_ready = Signal(object, str, int)  # PreviewStream | None, error, request id
    title_changed = Signal(str)
    fullscreen_changed = Signal(bool)
    popout_requested = Signal()  # the "play in a separate window" button

    def __init__(
        self,
        backend_factory: Callable[[QWidget], Backend] = create_backend,
        parent: QWidget | None = None,
        *,
        allow_popout: bool = False,
    ):
        super().__init__(parent)
        self.setObjectName("videoPlayer")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("QWidget#videoPlayer { background-color: #000000; }")

        self._backend = backend_factory(self)
        self._player = self._backend.player
        self._audio = self._backend.audio
        self._video = self._backend.video_widget
        self._video.setMouseTracking(True)
        self._video.setAutoFillBackground(True)
        self._video.setStyleSheet("background-color: #000000;")
        self._video.installEventFilter(self)

        self._video_id = ""
        self._title = ""
        self._page_url = ""
        self._stream_url = ""
        self._request_id = 0
        self._seeking = False
        self._boosted = False
        self._rate_before_boost = 1.0
        self._pending_error = ""
        self._pending_start_ms = 0
        self._remote = False
        self._forward_key: int | None = None
        # While fullscreen: the widget and layout slot to return to.
        self._home: tuple[QWidget, int] | None = None

        self._build_ui(allow_popout)
        self._wire_player()
        self._stream_ready.connect(self._on_stream_ready)

        self._hold_timer = QTimer(self)
        self._hold_timer.setSingleShot(True)
        self._hold_timer.setInterval(HOLD_BOOST_MS)
        self._hold_timer.timeout.connect(self._begin_boost)
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.timeout.connect(self.toggle_play)
        self._hide_controls_timer = QTimer(self)
        self._hide_controls_timer.setSingleShot(True)
        self._hide_controls_timer.setInterval(CONTROLS_HIDE_MS)
        self._hide_controls_timer.timeout.connect(self._hide_controls_if_fullscreen)

        volume = self._saved_volume()
        self._volume.setValue(int(volume * 100))
        self._audio.setVolume(volume)

    # ── UI ───────────────────────────────────────────────────────────────────

    def _wire_player(self):
        player = self._player
        player.positionChanged.connect(self._on_position)
        player.durationChanged.connect(self._on_duration)
        player.playbackStateChanged.connect(self._on_state)
        player.mediaStatusChanged.connect(self._on_media_status)
        player.errorOccurred.connect(self._on_error)

    # ── Loading ──────────────────────────────────────────────────────────────

    @property
    def video_id(self) -> str:
        return self._video_id

    @property
    def title(self) -> str:
        return self._title

    def position(self) -> int:
        return int(self._player.position() or 0)

    def play_local(self, path: str, title: str = "", video_id: str = "", *, start_ms: int = 0):
        self._begin_session(video_id, title, remote=False, start_ms=start_ms)
        self._load(QUrl.fromLocalFile(path))

    def play_remote(self, video_id: str, title: str = "", *, start_ms: int = 0):
        self._begin_session(video_id, title, remote=True, start_ms=start_ms)
        self._show_status(tr("Loading the video…", "正在加载视频…", "動画を読み込み中…"))
        request_id = self._request_id
        from ...core.manager import download_manager

        def work():
            try:
                stream = preview_stream.open_stream(download_manager.api, video_id)
                self._stream_ready.emit(stream, "", request_id)
            except Exception as exc:
                logger.warning("Preview stream failed for %s", video_id, exc_info=True)
                self._stream_ready.emit(None, str(exc), request_id)

        threading.Thread(target=work, name="iwara-preview-resolve", daemon=True).start()

    def stop(self):
        """Stop playback and release the stream (the player can be reused after)."""

        self._request_id += 1
        self._end_boost(restore=False)
        self._player.stop()
        self._player.setSource(QUrl())
        self._release_stream()
        self._pending_error = ""
        self._hide_status()

    def shutdown(self):
        """Stop for good: leave fullscreen, stop playback, remember the volume."""

        if self.is_fullscreen():
            self._leave_fullscreen()
        self.stop()
        self._save_volume()

    def _begin_session(self, video_id: str, title: str, *, remote: bool, start_ms: int = 0):
        self._request_id += 1
        self._release_stream()
        self._video_id = str(video_id or "")
        self._remote = remote
        self._page_url = f"https://www.iwara.tv/video/{self._video_id}" if self._video_id else ""
        self._browser_btn.setEnabled(bool(self._page_url))
        self._popout_btn.setEnabled(bool(self._video_id))
        self._pending_error = ""
        self._pending_start_ms = max(0, int(start_ms or 0))
        self._end_boost(restore=False)
        self._player.stop()
        self._set_rate(1.0)
        self._seek.setRange(0, 0)
        self._on_position(0)
        self._set_title(title)

    def _set_title(self, title: str):
        self._title = str(title or "")
        self.title_changed.emit(self._title)

    def _on_stream_ready(self, stream, error: str, request_id: int):
        if request_id != self._request_id:
            if stream is not None:
                preview_stream.get_proxy().unregister(stream.url)
            return  # a newer video was requested meanwhile
        if stream is None:
            self._pending_error = error or "error"
            self._show_status(
                tr(f"Cannot play this video: {error}", f"无法播放该视频：{error}", f"この動画を再生できません：{error}")
            )
            return
        self._stream_url = stream.url
        if stream.title and not self._title:
            self._set_title(stream.title)
        self._load(QUrl(stream.url))

    def _load(self, url: QUrl):
        self._player.setSource(url)
        self._player.play()

    def _release_stream(self):
        url, self._stream_url = self._stream_url, ""
        if url:
            preview_stream.get_proxy().unregister(url)

    # ── Public controls ──────────────────────────────────────────────────────

    def toggle_play(self):
        if self._is_playing():
            self._player.pause()
        else:
            if self._at_end():
                self._player.setPosition(0)
            self._player.play()

    def pause(self):
        if self._is_playing():
            self._player.pause()

    def seek_by(self, delta_ms: int):
        duration = int(self._player.duration() or 0)
        target = int(self._player.position() or 0) + int(delta_ms)
        target = max(0, min(target, duration) if duration else target)
        self._player.setPosition(target)

    # ── Event handling ───────────────────────────────────────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place_overlay()

    def closeEvent(self, event):
        # Alt+F4 on the fullscreen window: go back to the page, do not close.
        if self.is_fullscreen():
            event.ignore()
            self._leave_fullscreen()
            return
        super().closeEvent(event)

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _open_page(self):
        if self._page_url:
            webbrowser.open(self._page_url)
