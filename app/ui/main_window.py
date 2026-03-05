"""Main FluentWindow with sidebar navigation."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication

from qfluentwidgets import (
    FluentIcon,
    FluentWindow,
    NavigationItemPosition,
    Theme,
    isDarkTheme,
    setTheme,
)

from ..i18n import tr
from .download_page import DownloadInterface
from .settings_page import SettingsInterface
from .task_page import TaskCenterInterface


class MainWindow(FluentWindow):
    """Application main window using Fluent Design."""

    def __init__(self):
        super().__init__()
        self._init_window()
        self._init_navigation()
        self._splash_finish()

    def _init_window(self):
        self.setWindowTitle("IwaraTool")
        self.setMinimumSize(QSize(900, 640))
        self.resize(1100, 720)

        # Acrylic background (harmless on Windows 11, graceful fallback on W10)
        self.setMicaEffectEnabled(True)

    def _init_navigation(self):
        # Create sub-interfaces
        self._download_page = DownloadInterface(self)
        self._task_page = TaskCenterInterface(self)
        self._settings_page = SettingsInterface(self)

        # Add items with Fluent icons
        self.addSubInterface(
            self._download_page,
            icon=FluentIcon.DOWNLOAD,
            text=tr("New Download", "新建下载"),
        )
        self.addSubInterface(
            self._task_page,
            icon=FluentIcon.CHECKBOX,
            text=tr("Task Center", "任务中心"),
        )

        # Bottom quick actions (shown above settings)
        
        self.navigationInterface.addItem(
            routeKey="open-github",
            icon=FluentIcon.GITHUB,
            text="GitHub",
            onClick=self._open_github,
            selectable=False,
            position=NavigationItemPosition.BOTTOM,
            tooltip=tr("Open project GitHub (placeholder)", "打开项目 GitHub链接"),
        )

        # 切换模式暂时有问题, 先注释掉，后续再完善
        """
        self.navigationInterface.addItem(
            routeKey="toggle-theme",
            icon=FluentIcon.BRIGHTNESS,
            text=tr("Toggle Dark Mode", "切换暗黑模式"),
            onClick=self._toggle_dark_mode,
            selectable=False,
            position=NavigationItemPosition.BOTTOM,
            tooltip=tr("One-click theme toggle", "一键切换深浅色"),
        )
        """

        self.addSubInterface(
            self._settings_page,
            icon=FluentIcon.SETTING,
            text=tr("Settings", "应用设置"),
            position=NavigationItemPosition.BOTTOM,
        )

        # Default to download page
        self.switchTo(self._download_page)

    def _splash_finish(self):
        # If you have a splash screen, call finish here.
        # Currently a no-op.
        pass

    def _toggle_dark_mode(self):
        setTheme(Theme.LIGHT if isDarkTheme() else Theme.DARK)

    def _open_github(self):
        QDesktopServices.openUrl(QUrl("https://github.com/Moeary/IwaraTool"))
