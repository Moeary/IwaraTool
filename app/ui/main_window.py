"""Main FluentWindow with sidebar navigation."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import sys

from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import QApplication, QWidget

from qfluentwidgets import FluentWindow, qconfig

from ..signal_bus import signal_bus
from ..logging_setup import get_logger
from .navigation import NavigationController
from .notification_dispatcher import TaskNotificationBatch
from .shortcut_bindings import install_shortcuts
from .theme import install_accent
from .video_preview_window import VideoPreviewWindow, keep_normal_geometry
from .window_drag import WindowsTitleBarDragFilter
from .worker_lifecycle import ShutdownPoller
from .window.commands import WindowCommandsMixin
from .window.navigation import WindowNavigationMixin
from .window.notifications import WindowNotificationsMixin
from .window.preview import WindowPreviewMixin
from .window.shutdown import WindowShutdownMixin
from .window.updates import WindowUpdatesMixin


logger = get_logger(__name__)


class _NotificationBridge(QObject):
    """Deliver worker results back to the owning Qt event loop."""

    prepared = Signal(object)
    update_finished = Signal(str, str)  # downloaded path, error message


class _BackButtonFilter(QObject):
    """The mouse's back (X1) button goes back, wherever the pointer is in the window."""

    def __init__(self, window: "MainWindow"):
        super().__init__(window)
        self._window = window

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind in (
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseButtonRelease,
            QEvent.Type.MouseButtonDblClick,
        ) and event.button() == Qt.MouseButton.BackButton:
            if isinstance(watched, QWidget) and watched.window() is self._window:
                if kind == QEvent.Type.MouseButtonPress:
                    self._window.navigate_back()
                return True
        return False


class MainWindow(
    WindowNavigationMixin,
    WindowNotificationsMixin,
    WindowPreviewMixin,
    WindowUpdatesMixin,
    WindowCommandsMixin,
    WindowShutdownMixin,
    FluentWindow,
):
    """Application main window using Fluent Design."""

    _window_ref: "MainWindow | None" = None

    def __init__(self):
        install_accent()
        super().__init__()
        if sys.platform == "win32":
            self._title_bar_drag_filter = WindowsTitleBarDragFilter(self.titleBar)
        keep_normal_geometry(self)  # no drift after maximize/restore with a left taskbar
        self._reloading_language = False
        self._closing = False  # pages are stopping in the background; the window is hidden
        self._close_complete = False  # the next closeEvent is the real one
        self._shutdown_poller: ShutdownPoller | None = None
        self.navigation = NavigationController(
            resolve_page=self._page_of,
            current_page=lambda: self.stackedWidget.currentWidget(),
            switch_to=lambda page: FluentWindow.switchTo(self, page),
        )
        self._quitting = False
        self._tray_hint_shown = False
        self._pending_update_path = ""
        self._update_in_progress = False
        self._preview_window: VideoPreviewWindow | None = None
        self._trim_timer = QTimer(self)
        self._trim_timer.setSingleShot(True)
        self._trim_timer.setInterval(3000)
        self._trim_timer.timeout.connect(self._trim_memory_if_idle)
        self._task_notification_batch = TaskNotificationBatch()
        self._task_notification_timer = QTimer(self)
        self._task_notification_timer.setSingleShot(True)
        self._task_notification_timer.setInterval(180)
        self._task_notification_timer.timeout.connect(self._flush_task_notifications)
        self._task_notification_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="iwara-notification",
        )
        self._notification_bridge = _NotificationBridge(self)
        self._notification_bridge.prepared.connect(self._present_task_notifications)
        self._notification_bridge.update_finished.connect(self._on_update_downloaded)
        self._init_window()
        self._init_navigation()
        self.navigation.clear()  # opening on the startup page is not a step to go back to
        install_shortcuts(self)
        self._back_button_filter = _BackButtonFilter(self)
        QApplication.instance().installEventFilter(self._back_button_filter)
        self._init_desktop_notifications()
        self._splash_finish()
        signal_bus.language_changed.connect(self._on_language_changed)
        signal_bus.desktop_notification_requested.connect(self._show_desktop_notification)
        signal_bus.release_update_available.connect(self._on_release_update_available)
        signal_bus.video_preview_requested.connect(self._on_video_preview_requested)
        signal_bus.video_popout_requested.connect(self._on_video_popout_requested)
        signal_bus.task_status_changed.connect(self._on_task_status_notification)
        signal_bus.subscription_source_requested.connect(self._on_subscription_source_requested)
        signal_bus.media_detail_requested.connect(self._on_media_detail_requested)
        signal_bus.search_requested.connect(self._on_search_requested)
        signal_bus.author_page_requested.connect(self._on_author_page_requested)
        qconfig.themeChanged.connect(self._on_theme_changed)
        MainWindow._window_ref = self

    def _init_window(self):
        self.setWindowTitle("IwaraTool")
        self.setMinimumSize(QSize(900, 640))
        self.resize(1100, 720)

        # Acrylic background (harmless on Windows 11, graceful fallback on W10)
        self.setMicaEffectEnabled(True)

    # ── idle memory ──────────────────────────────────────────────────────────

    def showEvent(self, event):
        super().showEvent(event)
        self._trim_timer.stop()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._schedule_memory_trim()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange and self.isMinimized():
            self._schedule_memory_trim()
