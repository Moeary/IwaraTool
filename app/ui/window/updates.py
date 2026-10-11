"""Offer new GitHub releases; the user downloads them from the release page."""

from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices

from ...i18n import tr
from ...config import app_config
from ..fluent_dialogs import show_fluent_confirmation


class WindowUpdatesMixin:
    """Offer new GitHub releases; the user downloads them from the release page."""

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
