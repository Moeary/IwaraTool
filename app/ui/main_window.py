"""Main FluentWindow with sidebar navigation."""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import os
import sys
import threading

from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QCloseEvent, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QSystemTrayIcon,
    QTextEdit,
    QWidget,
)

from qfluentwidgets import (
    FluentIcon,
    FluentWindow,
    InfoBar,
    InfoBarPosition,
    NavigationItemPosition,
    qconfig,
    isDarkTheme,
)

from ..i18n import tr
from ..signal_bus import signal_bus
from ..config import app_config
from ..core import preview_stream, self_update, video_player
from ..core import memory, shortcut_defs
from ..core.manager import download_manager
from ..core.rating import RATING_ALL, RATINGS, UI_RATING_KEY, normalize_rating, rating_label
from ..logging_setup import get_logger
from .download_page import DownloadInterface
from .history_page import HistoryInterface
from .home_page import HomeInterface
from .navigation import NavEntry, NavigationController, draft_of, restore_draft_into, restore_into, snapshot_of
from .notification_dispatcher import (
    PreparedTaskNotifications,
    TaskNotificationBatch,
    prepare_task_notifications,
)
from .rules_page import RulesInterface
from .repair_page import RepairInterface
from .search_page import SearchInterface
from .settings_page import SettingsInterface
from .shortcut_bindings import install_shortcuts
from .subscription_page import SubscriptionInterface
from .theme import apply_theme_mode, install_accent, refresh_splitters
from .ui_state import show_fluent_confirmation
from .video_preview_window import VideoPreviewWindow, keep_normal_geometry
from .window_drag import WindowsTitleBarDragFilter
from .worker_lifecycle import ShutdownPoller


logger = get_logger(__name__)


PAGE_SHUTDOWN_TIMEOUT_MS = 30_000


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


class MainWindow(FluentWindow):
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

    def _init_navigation(self):
        # Create sub-interfaces
        self._home_page = HomeInterface(self)
        self._download_page = DownloadInterface(self)
        self._search_page = SearchInterface(self)
        self._subscription_page = SubscriptionInterface(self)
        self._history_page = HistoryInterface(self)
        self._repair_page = RepairInterface(self)
        self._rules_page = RulesInterface(self)
        self._settings_page = SettingsInterface(self)
        self._home_page.open_settings_requested.connect(
            lambda: self.switchTo(self._settings_page)
        )

        # Sidebar order: Home, Subscriptions, Search, Download Hub, Repair, History.
        self.addSubInterface(
            self._home_page,
            icon=FluentIcon.HOME,
            text=tr("Home", "首页", "ホーム"),
        )
        self.addSubInterface(
            self._subscription_page,
            icon=FluentIcon.PEOPLE,
            text=tr("Subscriptions", "订阅页", "購読"),
        )
        self.addSubInterface(
            self._search_page,
            icon=FluentIcon.SEARCH,
            text=tr("Search", "搜索", "検索"),
        )
        self.addSubInterface(
            self._download_page,
            icon=FluentIcon.DOWNLOAD,
            text=tr("Download Hub", "下载工作台", "ダウンロードハブ"),
        )
        self.addSubInterface(
            self._repair_page,
            icon=FluentIcon.FOLDER,
            text=tr("Repair", "修复", "修復"),
        )
        self.addSubInterface(
            self._history_page,
            icon=FluentIcon.HISTORY,
            text=tr("History", "历史记录", "履歴"),
        )
        # Bottom quick actions (shown above settings)

        self.navigationInterface.addItem(
            routeKey="open-github",
            icon=FluentIcon.GITHUB,
            text="GitHub",
            onClick=self._open_github,
            selectable=False,
            position=NavigationItemPosition.BOTTOM,
            tooltip=tr("Open project GitHub", "打开项目 GitHub链接", "プロジェクト GitHub を開く"),
        )

        self.navigationInterface.addItem(
            routeKey="toggle-theme",
            icon=FluentIcon.BRIGHTNESS,
            text=tr("Toggle Dark Mode", "切换黑夜模式", "ダークモード切替"),
            onClick=self._toggle_dark_mode,
            selectable=False,
            position=NavigationItemPosition.BOTTOM,
            tooltip=tr(
                "Switch between light and dark mode",
                "一键切换浅色/黑夜模式",
                "ライト/ダークモードを切り替えます",
            ),
        )

        self.addSubInterface(
            self._rules_page,
            icon=FluentIcon.FILTER,
            text=tr("Rules", "下载规则", "ルール"),
            position=NavigationItemPosition.BOTTOM,
        )

        self.addSubInterface(
            self._settings_page,
            icon=FluentIcon.SETTING,
            text=tr("Settings", "应用设置", "設定"),
            position=NavigationItemPosition.BOTTOM,
        )

        self.switchTo(self._startup_page())

    def _startup_page(self):
        """The page chosen in Settings → Window & Startup (Home by default)."""

        pages = {
            "home": self._home_page,
            "subscriptions": self._subscription_page,
            "search": self._search_page,
            "download": self._download_page,
            "repair": self._repair_page,
            "history": self._history_page,
            "rules": self._rules_page,
            "settings": self._settings_page,
        }
        return pages.get(str(app_config.startup_page or "").strip().lower(), self._home_page)

    def _splash_finish(self):
        # If you have a splash screen, call finish here.
        # Currently a no-op.
        pass

    # ── navigation ───────────────────────────────────────────────────────────

    def _pages(self) -> dict[str, QWidget]:
        """Every sidebar page by a stable key (survives a language rebuild)."""

        names = {
            "home": "_home_page",
            "download": "_download_page",
            "search": "_search_page",
            "subscriptions": "_subscription_page",
            "history": "_history_page",
            "repair": "_repair_page",
            "rules": "_rules_page",
            "settings": "_settings_page",
        }
        return {key: getattr(self, name) for key, name in names.items() if getattr(self, name, None) is not None}

    def _page_of(self, widget: QWidget | None) -> QWidget | None:
        pages = set(self._pages().values())
        while widget is not None and widget not in pages:
            widget = widget.parentWidget()
        return widget

    def switchTo(self, interface):
        """Any page switch (sidebar, shortcut, link) is a step Back can undo."""

        current = self.stackedWidget.currentWidget()
        if current is not None and interface is not current and hasattr(self, "navigation"):
            self.navigation.record(current)
        super().switchTo(interface)

    def _show_page(self, page: QWidget, show):
        """Record where the user is, then let ``show`` fill ``page`` and bring it up."""

        self.navigation.record(self.stackedWidget.currentWidget())
        with self.navigation.quiet():
            show()
            if self.stackedWidget.currentWidget() is not page:
                super().switchTo(page)

    def navigate_back(self) -> bool:
        """Back to the previous place in the history; nothing happens without one."""

        if self._closing:
            return False
        return self.navigation.back()

    def escape_back(self):
        """Esc: close what floats above the page first, then go back."""

        popup = QApplication.activePopupWidget()
        if popup is not None:
            popup.close()
            return
        focus = QApplication.focusWidget()
        if (
            isinstance(focus, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox))
            and focus.window() is self
        ):
            focus.clearFocus()  # leave the text box; the next Esc goes back
            return
        page = self.stackedWidget.currentWidget()
        dismiss = getattr(page, "dismiss_transient", None)
        if callable(dismiss) and dismiss():
            return
        self.navigate_back()

    def _on_subscription_source_requested(self, source_id: int):
        """Navigate after subscription_source_added has selected and refreshed."""

        source_id = int(source_id or 0)
        if not source_id:
            return
        self._show_page(self._subscription_page, lambda: self._subscription_page.show_source(source_id))

    def _on_media_detail_requested(self, kind: str, item_id: str):
        """Show a post in the Home detail view (e.g. from a search result)."""

        self._show_page(self._home_page, lambda: self._home_page.show_detail(kind, item_id, external=True))

    def _on_author_page_requested(self, target):
        """Open an author's in-app page (subscribed or not); Back returns to the caller."""

        self._show_page(
            self._subscription_page,
            lambda: self._subscription_page.show_author(tuple(target), external=True),
        )

    def _on_search_requested(self, request: dict):
        """Run an Iwara search on the Search page for another page; Back restores the caller."""

        def show():
            super(MainWindow, self).switchTo(self._search_page)
            self._search_page.apply_external_query(request)

        self._show_page(self._search_page, show)

    def _init_desktop_notifications(self):
        self._tray_icon: QSystemTrayIcon | None = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self._tray_icon = QSystemTrayIcon(QApplication.instance().windowIcon(), self)
        self._tray_icon.setToolTip("IwaraTool")
        self._tray_menu = QMenu()
        self._tray_menu.addAction(
            tr("Show / Hide", "显示 / 隐藏", "表示 / 非表示"), self._toggle_window_visibility
        )
        self._tray_menu.addSeparator()
        self._tray_menu.addAction(tr("Exit", "退出", "終了"), self._quit_from_tray)
        self._tray_icon.setContextMenu(self._tray_menu)
        self._tray_icon.activated.connect(self._on_tray_activated)
        self._tray_icon.show()

    def _on_tray_activated(self, reason):
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._toggle_window_visibility()

    def _toggle_window_visibility(self):
        if self.isVisible() and not self.isMinimized():
            self.hide()
            return
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _quit_from_tray(self):
        self._quitting = True
        self.showNormal()
        self.close()
        # The user may have kept the queue running; allow tray hiding again.
        if self.isVisible():
            self._quitting = False

    def _show_desktop_notification(self, title: str, message: str):
        signal_bus.log_message.emit(f"[{title}] {message}")
        if QApplication.activeWindow() is self:
            InfoBar.info(
                title=title,
                content=message,
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=5000,
                parent=self,
            )
        if not app_config.desktop_notifications_enabled or self._tray_icon is None:
            return
        self._tray_icon.showMessage(
            title,
            message,
            QSystemTrayIcon.MessageIcon.Information,
            8000,
        )

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
        window = self._preview_window
        if local:
            window.play_local(local, title, video_id, start_ms=start_ms)
        else:
            window.play_remote(video_id, title, start_ms=start_ms)
        window.show()
        window.raise_()
        window.activateWindow()
        window.setFocus()

    def _close_preview_window(self):
        window, self._preview_window = self._preview_window, None
        if window is not None:
            window.close()
            window.deleteLater()

    def _on_release_update_available(self, release: dict):
        version = str(release.get("version", "") or "")
        url = str(release.get("url", "") or "")
        manual = bool(release.get("manual", False))
        if not version or not url:
            return
        if not manual and version == app_config.update_last_prompted_version:
            return
        app_config.update_last_prompted_version = version
        installable = self._can_offer_self_update(release)
        self._show_desktop_notification(
            tr("IwaraTool update", "IwaraTool 更新", "IwaraTool 更新"),
            tr(
                f"Version {version} is available",
                f"发现新版本 {version}",
                f"新しいバージョン {version} があります",
            ),
        )
        if installable:
            self._offer_self_update(release, version)
            return
        if show_fluent_confirmation(
            self,
            tr("New Release Available", "发现新版本", "新しいリリース"),
            tr(
                f"IwaraTool {version} is available on GitHub Releases.",
                f"IwaraTool {version} 已发布到 GitHub Releases。",
                f"IwaraTool {version} が GitHub Releases に公開されました。",
            ),
            informative=tr(
                "Open the release page to review notes and download the update?",
                "是否打开 Release 页面查看说明并下载更新？",
                "リリースページを開いて内容を確認し、更新をダウンロードしますか？",
            ),
            yes_text=tr("Open Release", "打开 Release", "Release を開く"),
            no_text=tr("Later", "稍后", "後で"),
        ):
            QDesktopServices.openUrl(QUrl(url))

    def _can_offer_self_update(self, release: dict) -> bool:
        return bool(
            not self._update_in_progress
            and self_update.can_self_update()
            and release.get("asset_url")
            and release.get("asset_sha256")
        )

    def _offer_self_update(self, release: dict, version: str):
        """Offer a SHA-256 verified in-place update."""
        if show_fluent_confirmation(
            self,
            tr("New Release Available", "发现新版本", "新しいリリース"),
            tr(
                f"IwaraTool {version} is available.",
                f"发现新版本 {version}。",
                f"IwaraTool {version} が公開されました。",
            ),
            informative=tr(
                "Download it now? The file is verified with SHA-256 before the app restarts into the new version. Your data folder is kept.",
                "现在下载吗？文件会先通过 SHA-256 校验，再重启进入新版本；data 目录会保留。",
                "今すぐダウンロードしますか？SHA-256 で検証してから再起動します。data フォルダは保持されます。",
            ),
            yes_text=tr("Update now", "立即更新", "今すぐ更新"),
            no_text=tr("Later", "稍后", "後で"),
        ):
            self._start_self_update(release)

    def _start_self_update(self, release: dict):
        asset = self_update.UpdateAsset(
            name=str(release.get("asset_name", "")),
            url=str(release.get("asset_url", "")),
            sha256=str(release.get("asset_sha256", "")),
        )
        dest_dir = os.path.join(app_config.app_data_dir, "updates")
        dest_path = os.path.join(dest_dir, asset.name)
        self._update_in_progress = True
        signal_bus.log_message.emit(
            tr(
                f"[Update] Downloading {asset.name}…",
                f"[更新] 正在下载 {asset.name}…",
                f"[更新] {asset.name} をダウンロード中…",
            )
        )
        proxies = None
        if app_config.api_proxy_enabled and app_config.api_proxy_url:
            proxies = {"http": app_config.api_proxy_url, "https": app_config.api_proxy_url}
        bridge = self._notification_bridge

        def work():
            try:
                import cloudscraper

                path = self_update.download_verified(
                    asset, dest_path, session=cloudscraper.create_scraper(), proxies=proxies
                )
                bridge.update_finished.emit(path, "")
            except Exception as exc:
                logger.exception("Self-update download failed")
                bridge.update_finished.emit("", str(exc))

        threading.Thread(target=work, name="iwara-self-update", daemon=True).start()

    def _on_update_downloaded(self, path: str, error: str):
        self._update_in_progress = False
        if error or not path:
            self._show_desktop_notification(
                tr("IwaraTool update", "IwaraTool 更新", "IwaraTool 更新"),
                tr(
                    f"Update failed: {error}",
                    f"更新失败：{error}",
                    f"更新に失敗しました：{error}",
                ),
            )
            return
        if show_fluent_confirmation(
            self,
            tr("Update ready", "更新已就绪", "更新の準備完了"),
            tr(
                "The new version was downloaded and verified.",
                "新版本已下载并通过校验。",
                "新しいバージョンをダウンロードし、検証しました。",
            ),
            informative=tr(
                "Restart now to install it? Queued tasks are saved and restored automatically.",
                "现在重启安装吗？排队任务会被保存并自动恢复。",
                "今すぐ再起動してインストールしますか？キュー内のタスクは保存され、自動復元されます。",
            ),
            yes_text=tr("Restart now", "立即重启", "今すぐ再起動"),
            no_text=tr("Later", "稍后", "後で"),
        ):
            self._pending_update_path = path
            self._quitting = True
            self.close()
            if self.isVisible():  # closing was declined (e.g. pending-task prompt)
                self._pending_update_path = ""
                self._quitting = False

    def _on_task_status_notification(self, task_id: str, status: str):
        if self._task_notification_executor is None or status not in {"completed", "failed"}:
            return
        if not self._task_notification_batch.add(task_id, status):
            return
        # Do not build or animate an InfoBar from every worker completion.
        # The short debounce combines bursts into one paint operation.
        if not self._task_notification_timer.isActive():
            self._task_notification_timer.start()

    def _flush_task_notifications(self):
        events = self._task_notification_batch.drain()
        if not events or self._task_notification_executor is None:
            return
        future = self._task_notification_executor.submit(
            prepare_task_notifications,
            events,
            download_manager.get_task,
        )
        future.add_done_callback(self._on_task_notifications_prepared)

    def _on_task_notifications_prepared(self, future: Future):
        if self._task_notification_executor is None:
            return
        try:
            prepared = future.result()
        except Exception as exc:
            signal_bus.log_message.emit(f"[Notification] {exc}")
            return
        if isinstance(prepared, PreparedTaskNotifications):
            # Emitting from the worker is safe; the bridge's receiver lives in
            # the GUI thread, so Qt queues this slot for the next event turn.
            self._notification_bridge.prepared.emit(prepared)

    def _present_task_notifications(self, prepared: PreparedTaskNotifications):
        completed = prepared.completed_titles
        failed = prepared.failed_messages
        if not completed and not failed:
            return

        if len(completed) == 1 and not failed:
            self._show_desktop_notification(
                tr("Download completed", "下载完成", "ダウンロード完了"),
                completed[0],
            )
            return
        if len(failed) == 1 and not completed:
            self._show_desktop_notification(
                tr("Download failed", "下载失败", "ダウンロード失敗"),
                failed[0],
            )
            return

        parts: list[str] = []
        if completed:
            parts.append(
                tr(
                    f"{len(completed)} downloads completed",
                    f"{len(completed)} 个下载已完成",
                    f"{len(completed)} 件のダウンロードが完了",
                )
            )
        if failed:
            parts.append(
                tr(
                    f"{len(failed)} downloads failed",
                    f"{len(failed)} 个下载失败",
                    f"{len(failed)} 件のダウンロードに失敗",
                )
            )
        self._show_desktop_notification(
            tr("Download updates", "下载任务更新", "ダウンロード更新"),
            "；".join(parts),
        )

    def _shutdown_task_notification_worker(self):
        self._task_notification_timer.stop()
        self._task_notification_batch.clear()
        executor = getattr(self, "_task_notification_executor", None)
        if executor is not None:
            self._task_notification_executor = None
            executor.shutdown(wait=False, cancel_futures=True)

    def quick_download(self):
        """Ctrl+Alt+V: paste the clipboard link and queue it, from any page."""

        self.switchTo(self._download_page)
        self._download_page._paste_from_clipboard(submit=True)

    def open_download_folder(self):
        folder = app_config.download_dir
        if folder and os.path.isdir(folder):
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))
            return
        InfoBar.warning(
            title=tr("Download folder not found", "找不到下载文件夹", "保存フォルダーが見つかりません"),
            content=str(folder or ""),
            orient=Qt.Orientation.Horizontal, isClosable=True, position=InfoBarPosition.TOP, duration=3500, parent=self,
        )

    def cycle_rating(self):
        """Ctrl+Shift+R: All → SFW → NSFW for Home and Search together."""

        current = normalize_rating(app_config.get_ui_value(UI_RATING_KEY, RATING_ALL))
        following = RATINGS[(RATINGS.index(current) + 1) % len(RATINGS)]
        app_config.set_ui_value(UI_RATING_KEY, following)
        signal_bus.content_rating_changed.emit(following)
        InfoBar.info(
            title=tr("Content filter", "内容分级", "コンテンツ区分"),
            content=rating_label(following),
            orient=Qt.Orientation.Horizontal, isClosable=True, position=InfoBarPosition.TOP, duration=1800, parent=self,
        )

    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def show_shortcut_help(self):
        """F1: the cheat sheet; its button opens Settings → Keyboard Shortcuts."""

        from .shortcut_help import ShortcutHelpDialog

        page = self.stackedWidget.currentWidget()
        scope = {
            self._home_page: shortcut_defs.SCOPE_HOME,
            self._search_page: shortcut_defs.SCOPE_SEARCH,
            self._subscription_page: shortcut_defs.SCOPE_SUBSCRIPTIONS,
            self._download_page: shortcut_defs.SCOPE_DOWNLOAD,
            self._history_page: shortcut_defs.SCOPE_HISTORY,
            self._repair_page: shortcut_defs.SCOPE_REPAIR,
            self._rules_page: shortcut_defs.SCOPE_RULES,
            self._settings_page: shortcut_defs.SCOPE_SETTINGS,
        }.get(page, "")
        dialog = ShortcutHelpDialog(self, scope)
        dialog.exec()
        if dialog.customize_requested:
            self.switchTo(self._settings_page)
            self._settings_page.show_shortcut_settings()

    def _toggle_dark_mode(self):
        # Persist the explicit choice so the next launch opens in the same mode.
        mode = "light" if isDarkTheme() else "dark"
        app_config.theme_mode = mode
        apply_theme_mode(mode)
        self._refresh_theme_styles()

    def _on_theme_changed(self, *_args):
        self._refresh_theme_styles()

    def _refresh_theme_styles(self):
        refresh_splitters(self)
        for page in (
            getattr(self, "_home_page", None),
            getattr(self, "_download_page", None),
            getattr(self, "_search_page", None),
            getattr(self, "_subscription_page", None),
            getattr(self, "_history_page", None),
            getattr(self, "_repair_page", None),
            getattr(self, "_rules_page", None),
            getattr(self, "_settings_page", None),
        ):
            refresh = getattr(page, "refresh_theme_styles", None)
            if refresh:
                refresh()

    def _open_github(self):
        QDesktopServices.openUrl(QUrl("https://github.com/Moeary/IwaraTool"))

    # ── language rebuild ─────────────────────────────────────────────────────

    def export_session(self) -> dict:
        """Where the user is and how they got there, as plain data for a new window."""

        keys = {page: key for key, page in self._pages().items()}
        current = self.stackedWidget.currentWidget()
        return {
            "current": keys.get(current, ""),
            "state": snapshot_of(current) if current is not None else None,
            "drafts": {key: draft for key, page in self._pages().items() if (draft := draft_of(page)) is not None},
            "history": [
                (keys[entry.page], entry.state)
                for entry in self.navigation.entries()
                if entry.page in keys
            ],
        }

    def import_session(self, session: dict):
        pages = self._pages()
        page = pages.get(str(session.get("current") or ""))
        for key, draft in (session.get("drafts") or {}).items():
            if key in pages:
                restore_draft_into(pages[key], draft)
        with self.navigation.quiet():
            if page is not None:
                super().switchTo(page)
                restore_into(page, session.get("state"))
        self.navigation.replace([
            NavEntry(pages[key], state) for key, state in session.get("history") or [] if key in pages
        ])

    def _on_language_changed(self, _lang: str):
        """Rebuild the window in the new language, carrying the user's place over.

        Texts are set when widgets are built, so a fresh window is the reliable
        way to translate everything; the old one stops its work in the
        background and is then destroyed, with its shortcuts, tray icon and
        signal connections.
        """

        if self._reloading_language:
            return
        self._reloading_language = True
        session = self.export_session()
        new_window = MainWindow()
        new_window.import_session(session)
        if self.isMaximized():
            new_window.showMaximized()
        else:
            new_window.resize(self.size())
            new_window.move(self.pos())
            new_window.show()
        MainWindow._window_ref = new_window
        self.close()

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

    def _schedule_memory_trim(self):
        if not self._reloading_language and not self._quitting:
            self._trim_timer.start()

    def _trim_memory_if_idle(self):
        """Nobody is looking (minimized or in the tray): hand unused pages back to the OS."""

        if not self.isVisible() or self.isMinimized():
            memory.trim_memory()

    def closeEvent(self, event: QCloseEvent):
        """Persist active work before the final application window closes.

        Page workers are stopped without blocking the GUI thread: all of them
        are cancelled at once, the window hides, and the close completes when
        they report done (or the shared deadline passes).
        """
        if self._close_complete:
            super().closeEvent(event)
            return
        if self._closing:
            event.ignore()
            return
        if self._reloading_language or MainWindow._window_ref is not self:
            self._retire(event)
            return

        if (
            app_config.minimize_to_tray
            and self._tray_icon is not None
            and not self._quitting
        ):
            event.ignore()
            self.hide()
            if not self._tray_hint_shown:
                self._tray_hint_shown = True
                self._tray_icon.showMessage(
                    "IwaraTool",
                    tr(
                        "Still running in the system tray. Use the tray menu to exit.",
                        "仍在系统托盘中运行，可通过托盘菜单退出。",
                        "システムトレイで動作中です。終了するにはトレイメニューを使用してください。",
                    ),
                    QSystemTrayIcon.MessageIcon.Information,
                    5000,
                )
            return

        pending = download_manager.pending_task_count()
        if pending and not show_fluent_confirmation(
            self,
            tr("Exit IwaraTool", "退出 IwaraTool", "IwaraTool を終了"),
            tr(
                f"{pending} tasks are still queued or running.",
                f"仍有 {pending} 个任务正在排队或运行。",
                f"{pending} 件のタスクが待機中または実行中です。",
            ),
            informative=tr(
                "The queue and temporary files will be kept and restored automatically next time.",
                "队列与临时文件会被保留，下次启动时自动恢复。",
                "キューと一時ファイルを保持し、次回起動時に自動復元します。",
            ),
            yes_text=tr("Save and exit", "保存队列并退出", "保存して終了"),
            no_text=tr("Keep running", "继续运行", "実行を続ける"),
        ):
            event.ignore()
            return

        self._close_preview_window()
        self._shutdown_task_notification_worker()
        if self._start_page_shutdown(self._on_exit_pages_stopped):
            self._finish_exit()
            super().closeEvent(event)
            return
        event.ignore()
        self._hide_while_stopping()

    def _page_shutdown_steps(self) -> list:
        return [
            page.shutdown
            for page in (
                getattr(self, "_home_page", None),
                getattr(self, "_search_page", None),
                getattr(self, "_subscription_page", None),
                getattr(self, "_repair_page", None),
                getattr(self, "_settings_page", None),
            )
            if callable(getattr(page, "shutdown", None))
        ]

    def _start_page_shutdown(self, on_done) -> bool:
        """Cancel every page's workers at once; True if none had to be waited for.

        Otherwise ``on_done(ok)`` runs once they have stopped (``ok``) or the
        shared deadline passed, while the event loop keeps running.
        """

        poller = ShutdownPoller(self._page_shutdown_steps(), timeout_ms=PAGE_SHUTDOWN_TIMEOUT_MS, parent=self)
        if poller.start():
            poller.deleteLater()
            return True
        self._closing = True
        self._shutdown_poller = poller
        poller.done.connect(on_done)
        return False

    def _hide_while_stopping(self):
        self._closing = True
        self._trim_timer.stop()
        if self._tray_icon is not None:
            self._tray_icon.hide()
        self.hide()

    def _finish_exit(self):
        from ..core.background_services import background_service

        background_service.stop(wait=False)
        self._shutdown_task_notification_worker()
        self._close_preview_window()
        preview_stream.shutdown_proxy()
        download_manager.shutdown(wait=False)
        if self._pending_update_path:
            try:
                self_update.launch_swap_script(
                    self._pending_update_path,
                    os.path.join(app_config.app_data_dir, "updates"),
                )
            except Exception:
                logger.exception("Could not start the update installer")
        MainWindow._window_ref = None

    def _on_exit_pages_stopped(self, ok: bool):
        self._shutdown_poller = None
        self._finish_exit()
        self._close_complete = True
        self.close()
        if ok:
            QApplication.quit()
            return
        # A page worker is stuck in a request past the deadline.  Destroying a
        # running QThread would crash, so leave without tearing Qt down; the
        # queue and settings were already saved above.
        logger.warning("Page workers did not stop within %d ms; exiting anyway", PAGE_SHUTDOWN_TIMEOUT_MS)
        app_config.sync()
        import logging

        logging.shutdown()
        os._exit(0)

    def _retire(self, event: QCloseEvent):
        """Close a window that was replaced (language change): stop, then destroy it."""

        self._close_preview_window()
        self._shutdown_task_notification_worker()
        if self._tray_icon is not None:
            self._tray_icon.hide()
        tray_menu = getattr(self, "_tray_menu", None)
        if tray_menu is not None:
            tray_menu.deleteLater()
        if self._start_page_shutdown(self._on_retired_pages_stopped):
            self._close_complete = True
            super().closeEvent(event)
            self.deleteLater()
            return
        event.ignore()
        self._hide_while_stopping()

    def deleteLater(self):
        """Never destroy the window (and its pages' running QThreads) mid-shutdown.

        While pages are still stopping in the background, deletion is deferred
        until they have; destroying a running QThread would abort the process.
        """
        if self._closing and not self._close_complete:
            return  # _on_retired_pages_stopped deletes it once its workers have stopped
        super().deleteLater()

    def _on_retired_pages_stopped(self, ok: bool):
        self._shutdown_poller = None
        if not ok:
            # Still busy: keep waiting while hidden, never destroy a running thread.
            self._closing = False
            if not self._start_page_shutdown(self._on_retired_pages_stopped):
                return
        self._close_complete = True
        self.close()
        self.deleteLater()
