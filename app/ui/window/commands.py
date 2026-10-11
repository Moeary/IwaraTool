"""Window-wide commands: quick download, rating, theme, language, fullscreen, session export."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices

from qfluentwidgets import InfoBar, InfoBarPosition, isDarkTheme

from ...i18n import tr
from ...signal_bus import signal_bus
from ...config import app_config
from ...core import shortcut_defs
from ...core.rating import RATING_ALL, RATINGS, UI_RATING_KEY, normalize_rating, rating_label
from ..navigation import NavEntry, draft_of, restore_draft_into, restore_into, snapshot_of
from ..theme import apply_theme_mode, refresh_splitters


class WindowCommandsMixin:
    """Window-wide commands: quick download, rating, theme, language, fullscreen, session export."""

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

        from ..shortcut_help import ShortcutHelpDialog

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
        from ..main_window import MainWindow

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
