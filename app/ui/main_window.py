"""Main FluentWindow with sidebar navigation."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtGui import QCloseEvent, QDesktopServices
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from qfluentwidgets import (
    FluentIcon,
    FluentWindow,
    InfoBar,
    InfoBarPosition,
    NavigationItemPosition,
    Theme,
    qconfig,
    isDarkTheme,
    setTheme,
)

from ..i18n import tr
from ..signal_bus import signal_bus
from ..config import app_config
from ..core.manager import download_manager
from .download_page import DownloadInterface
from .history_page import HistoryInterface
from .rules_page import RulesInterface
from .search_page import SearchInterface
from .settings_page import SettingsInterface
from .subscription_page import SubscriptionInterface
from .ui_state import show_fluent_confirmation


class MainWindow(FluentWindow):
    """Application main window using Fluent Design."""

    _window_ref: "MainWindow | None" = None

    def __init__(self):
        super().__init__()
        self._reloading_language = False
        self._init_window()
        self._init_navigation()
        self._init_desktop_notifications()
        self._splash_finish()
        signal_bus.language_changed.connect(self._on_language_changed)
        signal_bus.desktop_notification_requested.connect(self._show_desktop_notification)
        signal_bus.release_update_available.connect(self._on_release_update_available)
        signal_bus.task_status_changed.connect(self._on_task_status_notification)
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
        self._download_page = DownloadInterface(self)
        self._search_page = SearchInterface(self)
        self._subscription_page = SubscriptionInterface(self)
        self._history_page = HistoryInterface(self)
        self._rules_page = RulesInterface(self)
        self._settings_page = SettingsInterface(self)

        # Add items with Fluent icons
        self.addSubInterface(
            self._download_page,
            icon=FluentIcon.DOWNLOAD,
            text=tr("Download Hub", "下载工作台", "ダウンロードハブ"),
        )
        self.addSubInterface(
            self._search_page,
            icon=FluentIcon.SEARCH,
            text=tr("Search", "搜索", "検索"),
        )
        self.addSubInterface(
            self._subscription_page,
            icon=FluentIcon.PEOPLE,
            text=tr("Subscriptions", "订阅页", "購読"),
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

        # Default to download page
        self.switchTo(self._download_page)

    def _splash_finish(self):
        # If you have a splash screen, call finish here.
        # Currently a no-op.
        pass

    def _init_desktop_notifications(self):
        self._tray_icon: QSystemTrayIcon | None = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self._tray_icon = QSystemTrayIcon(QApplication.instance().windowIcon(), self)
        self._tray_icon.setToolTip("IwaraTool")
        self._tray_icon.show()

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

    def _on_release_update_available(self, release: dict):
        version = str(release.get("version", "") or "")
        url = str(release.get("url", "") or "")
        manual = bool(release.get("manual", False))
        if not version or not url:
            return
        if not manual and version == app_config.update_last_prompted_version:
            return
        app_config.update_last_prompted_version = version
        self._show_desktop_notification(
            tr("IwaraTool update", "IwaraTool 更新", "IwaraTool 更新"),
            tr(
                f"Version {version} is available",
                f"发现新版本 {version}",
                f"新しいバージョン {version} があります",
            ),
        )
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

    def _on_task_status_notification(self, task_id: str, status: str):
        if status not in {"completed", "failed"}:
            return
        task = download_manager.get_task(task_id)
        if task is None:
            return
        title = task.title or task.video_id
        if status == "completed":
            self._show_desktop_notification(
                tr("Download completed", "下载完成", "ダウンロード完了"),
                title,
            )
        else:
            self._show_desktop_notification(
                tr("Download failed", "下载失败", "ダウンロード失敗"),
                f"{title}: {task.error_msg}",
            )

    def _toggle_dark_mode(self):
        setTheme(Theme.LIGHT if isDarkTheme() else Theme.DARK)
        self._refresh_theme_styles()

    def _on_theme_changed(self, *_args):
        self._refresh_theme_styles()

    def _refresh_theme_styles(self):
        for page in (
            getattr(self, "_download_page", None),
            getattr(self, "_search_page", None),
            getattr(self, "_subscription_page", None),
            getattr(self, "_history_page", None),
            getattr(self, "_rules_page", None),
            getattr(self, "_settings_page", None),
        ):
            refresh = getattr(page, "refresh_theme_styles", None)
            if refresh:
                refresh()

    def _open_github(self):
        QDesktopServices.openUrl(QUrl("https://github.com/Moeary/IwaraTool"))

    def _on_language_changed(self, _lang: str):
        if self._reloading_language:
            return
        self._reloading_language = True
        new_window = MainWindow()
        if self.isMaximized():
            new_window.showMaximized()
        else:
            new_window.resize(self.size())
            new_window.move(self.pos())
            new_window.show()
        MainWindow._window_ref = new_window
        self.close()

    def closeEvent(self, event: QCloseEvent):
        """Persist active work before the final application window closes."""
        if self._reloading_language or MainWindow._window_ref is not self:
            if not self._shutdown_page_workers():
                event.ignore()
                return
            super().closeEvent(event)
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

        if not self._shutdown_page_workers():
            signal_bus.log_message.emit(
                tr(
                    "[Exit] Background page operations are still stopping; close again after they finish.",
                    "[退出] 页面后台操作仍在停止，请稍后再次关闭。",
                    "[終了] ページのバックグラウンド処理を停止中です。完了後に再度終了してください。",
                )
            )
            event.ignore()
            return

        from ..core.background_services import background_service

        background_service.stop(wait=False)
        download_manager.shutdown(wait=False)
        MainWindow._window_ref = None
        super().closeEvent(event)

    def _shutdown_page_workers(self) -> bool:
        for page in (
            getattr(self, "_search_page", None),
            getattr(self, "_subscription_page", None),
            getattr(self, "_settings_page", None),
        ):
            shutdown = getattr(page, "shutdown", None)
            if callable(shutdown) and not shutdown(timeout_ms=30_000):
                return False
        return True
