"""Sidebar pages, page switching and back navigation of the main window."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QLineEdit,
    QPlainTextEdit,
    QTextEdit,
    QWidget,
)

from qfluentwidgets import FluentIcon, NavigationItemPosition

from ...i18n import tr
from ...config import app_config
from ..downloads.page import DownloadInterface
from ..history.page import HistoryInterface
from ..home.page import HomeInterface
from ..rules.page import RulesInterface
from ..repair.page import RepairInterface
from ..search.page import SearchInterface
from ..settings.page import SettingsInterface
from ..subscription.page import SubscriptionInterface
from .staging_badge import StagingBadge


class WindowNavigationMixin:
    """Sidebar pages, page switching and back navigation of the main window."""

    def _init_navigation(self):
        # Create sub-interfaces
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

        self._add_main_pages()
        self._add_bottom_items()
        self._staging_badge = StagingBadge(self.navigationInterface, self._download_nav_item)

        self.switchTo(self._startup_page())

    def _add_main_pages(self):
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
        self._download_nav_item = self.addSubInterface(
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

    def _add_bottom_items(self):
        """Quick actions, Rules and Settings at the bottom of the sidebar."""


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
            super(WindowNavigationMixin, self).switchTo(self._search_page)
            self._search_page.apply_external_query(request)

        self._show_page(self._search_page, show)
