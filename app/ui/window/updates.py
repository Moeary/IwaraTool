"""Offer, download and apply new GitHub releases."""

from __future__ import annotations

import os
import threading

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices

from ...i18n import tr
from ...signal_bus import signal_bus
from ...config import app_config
from ...core import self_update
from ...logging_setup import get_logger
from ..fluent_dialogs import show_fluent_confirmation

logger = get_logger(__name__)


class WindowUpdatesMixin:
    """Offer, download and apply new GitHub releases."""

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
