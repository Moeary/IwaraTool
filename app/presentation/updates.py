"""Offering a new GitHub release (both interfaces): when to ask and what to say."""
from __future__ import annotations

from dataclasses import dataclass

from ..config import app_config
from ..i18n import tr


@dataclass(frozen=True)
class ReleaseOffer:
    version: str
    url: str

    @property
    def notification(self) -> tuple[str, str]:
        v = self.version
        return tr("IwaraTool update", "IwaraTool 更新", "IwaraTool 更新"), tr(
            f"Version {v} is available", f"发现新版本 {v}", f"新しいバージョン {v} があります")

    @property
    def question(self) -> tuple[str, str, str, str, str]:
        """``(title, message, informative, open label, later label)``."""
        v = self.version
        return (
            tr("New Release Available", "发现新版本", "新しいリリース"),
            tr(f"IwaraTool {v} is available on GitHub Releases.", f"IwaraTool {v} 已发布到 GitHub Releases。",
               f"IwaraTool {v} が GitHub Releases に公開されました。"),
            tr("Open the release page to review notes and download the update?", "是否打开 Release 页面查看说明并下载更新？",
               "リリースページを開いて内容を確認し、更新をダウンロードしますか？"),
            tr("Open Release", "打开 Release", "Release を開く"),
            tr("Later", "稍后", "後で"),
        )


def release_offer(release: dict) -> ReleaseOffer | None:
    """The offer for ``release_update_available``, or None (incomplete, or already offered automatically)."""
    version = str(release.get("version", "") or "")
    url = str(release.get("url", "") or "")
    if not version or not url:
        return None
    if not release.get("manual", False) and version == app_config.update_last_prompted_version:
        return None
    app_config.update_last_prompted_version = version
    return ReleaseOffer(version, url)
