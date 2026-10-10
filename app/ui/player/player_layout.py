"""Video surface, control bar and status overlay of the player."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from qfluentwidgets import ComboBox, FluentIcon, Slider, ToolButton, isDarkTheme, qconfig

from ...i18n import tr
from .playback import SPEEDS


class PlayerLayoutMixin:
    """Video surface, control bar and status overlay of the player."""

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
