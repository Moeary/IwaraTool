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
from dataclasses import dataclass
from typing import Any, Callable

from PySide6.QtCore import QEvent, QObject, QRect, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from qfluentwidgets import ComboBox, FluentIcon, FluentWidget, Slider, ToolButton, isDarkTheme, qconfig

from ..config import app_config
from ..core import preview_stream
from ..i18n import tr
from ..logging_setup import get_logger
from ..qt_runtime import ensure_multimedia_plugins
from .shortcuts import action_for_event

logger = get_logger(__name__)

SPEEDS = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0)
HOLD_BOOST_MS = 350
HOLD_BOOST_RATE = 2.0
SEEK_STEP_MS = 5000
VOLUME_STEP = 0.05
CONTROLS_HIDE_MS = 2500


def format_time(milliseconds: int) -> str:
    seconds = max(0, int(milliseconds)) // 1000
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def nearest_speed_index(rate: float) -> int:
    return min(range(len(SPEEDS)), key=lambda i: abs(SPEEDS[i] - rate))


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


class VideoPlayer(QWidget):
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

    def _build_ui(self, allow_popout: bool):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._video, 1)

        self._overlay = QLabel(self._video)
        self._overlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._overlay.setWordWrap(True)
        self._overlay.setStyleSheet(
            "QLabel { color: white; background: rgba(0,0,0,150); border-radius: 8px; padding: 10px 16px; font-size: 14px; }"
        )
        self._overlay.hide()
        self._overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        self._controls = QWidget(self)
        self._controls.setObjectName("previewControls")
        bar = QHBoxLayout(self._controls)
        bar.setContentsMargins(12, 8, 12, 8)
        bar.setSpacing(8)

        self._play_btn = ToolButton(FluentIcon.PLAY, self._controls)
        self._play_btn.clicked.connect(self.toggle_play)
        bar.addWidget(self._play_btn)

        self._time_label = QLabel("00:00", self._controls)
        bar.addWidget(self._time_label)

        self._seek = Slider(Qt.Orientation.Horizontal, self._controls)
        self._seek.setRange(0, 0)
        self._seek.sliderPressed.connect(self._on_seek_pressed)
        self._seek.sliderReleased.connect(self._on_seek_released)
        self._seek.sliderMoved.connect(self._on_seek_moved)
        if hasattr(self._seek, "clicked"):
            self._seek.clicked.connect(self._on_seek_clicked)
        bar.addWidget(self._seek, 1)

        self._duration_label = QLabel("00:00", self._controls)
        bar.addWidget(self._duration_label)

        self._speed = ComboBox(self._controls)
        for rate in SPEEDS:
            self._speed.addItem(f"{rate:g}x")
        self._speed.setCurrentIndex(SPEEDS.index(1.0))
        self._speed.setFixedWidth(84)
        self._speed.currentIndexChanged.connect(self._on_speed_changed)
        bar.addWidget(self._speed)

        self._mute_btn = ToolButton(FluentIcon.VOLUME, self._controls)
        self._mute_btn.clicked.connect(self.toggle_mute)
        bar.addWidget(self._mute_btn)
        self._volume = Slider(Qt.Orientation.Horizontal, self._controls)
        self._volume.setRange(0, 100)
        self._volume.setFixedWidth(96)
        self._volume.valueChanged.connect(self._on_volume_changed)
        bar.addWidget(self._volume)

        self._browser_btn = ToolButton(FluentIcon.GLOBE, self._controls)
        self._browser_btn.setToolTip(tr("Open the Iwara page", "在浏览器打开 Iwara 页面", "ブラウザーでIwaraページを開く"))
        self._browser_btn.clicked.connect(self._open_page)
        bar.addWidget(self._browser_btn)

        self._popout_btn = ToolButton(FluentIcon.APPLICATION, self._controls)
        self._popout_btn.setToolTip(
            tr("Play in a separate window", "在独立窗口中播放", "別ウィンドウで再生")
        )
        self._popout_btn.clicked.connect(self._request_popout)
        self._popout_btn.setVisible(allow_popout)
        bar.addWidget(self._popout_btn)

        self._fullscreen_btn = ToolButton(FluentIcon.FULL_SCREEN, self._controls)
        self._fullscreen_btn.setToolTip(tr("Fullscreen (F)", "全屏 (F)", "全画面 (F)"))
        self._fullscreen_btn.clicked.connect(self.toggle_fullscreen)
        bar.addWidget(self._fullscreen_btn)
        root.addWidget(self._controls)
        # Keyboard shortcuts belong to the player; a focused button would eat Space.
        for child in self._controls.findChildren(QWidget):
            child.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.refresh_theme_styles()
        qconfig.themeChanged.connect(self.refresh_theme_styles)

    def refresh_theme_styles(self, *_args):
        """The picture stays black; the control bar follows the app's light/dark theme."""
        dark = isDarkTheme()
        background, text = ("#202020", "#e6e6e6") if dark else ("#f3f3f3", "#202020")
        self._controls.setStyleSheet(
            f"QWidget#previewControls {{ background: {background}; }}"
            f"QLabel {{ color: {text}; background: transparent; }}"
        )

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
        from ..core.manager import download_manager

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

    # ── Fullscreen ───────────────────────────────────────────────────────────

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

    # ── Hold-to-boost ────────────────────────────────────────────────────────

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

    # ── Event handling ───────────────────────────────────────────────────────

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

    # ── Player callbacks ─────────────────────────────────────────────────────

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

    # ── Slider / combo callbacks ─────────────────────────────────────────────

    def _on_seek_pressed(self):
        self._seeking = True

    def _on_seek_moved(self, value: int):
        self._time_label.setText(format_time(value))

    def _on_seek_released(self):
        self._player.setPosition(int(self._seek.value()))
        self._seeking = False

    def _on_seek_clicked(self, value: int):
        self._player.setPosition(int(value))

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

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _is_playing(self) -> bool:
        from PySide6.QtMultimedia import QMediaPlayer

        return self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def _at_end(self) -> bool:
        duration = int(self._player.duration() or 0)
        return duration > 0 and int(self._player.position() or 0) >= duration - 200

    def _show_status(self, text: str):
        self._overlay.setText(text)
        self._overlay.adjustSize()
        self._place_overlay()
        self._overlay.show()
        self._overlay.raise_()

    def _hide_status(self):
        if not self._pending_error:
            self._overlay.hide()

    def _place_overlay(self):
        width = min(self._video.width() - 40, max(240, self._overlay.sizeHint().width()))
        self._overlay.setFixedWidth(max(120, width))
        self._overlay.adjustSize()
        self._overlay.move(max(0, (self._video.width() - self._overlay.width()) // 2), 24)

    def _hide_controls_if_fullscreen(self):
        if self.is_fullscreen():
            self._controls.hide()
            self._video.setCursor(Qt.CursorShape.BlankCursor)

    def _open_page(self):
        if self._page_url:
            webbrowser.open(self._page_url)

    @staticmethod
    def _saved_volume() -> float:
        try:
            return max(0.0, min(1.0, float(app_config.get_ui_value("preview_volume_v1", 0.8))))
        except (TypeError, ValueError):
            return 0.8

    def _save_volume(self):
        app_config.set_ui_value("preview_volume_v1", round(float(self._audio.volume()), 2))


class VideoPreviewWindow(FluentWidget):
    """One reusable Fluent window (themed title bar, Mica) around a ``VideoPlayer``."""

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
