"""In-app author page and the "how do I follow this person" status bar.

Opening an author from Search (or anywhere else) lands here instead of the
website: the page shows their works as posters, whether the author is in this
app's local subscriptions, whether the signed-in account follows them on Iwara,
and lets you change either from one place.  Authors that are already local
subscriptions open on the subscription grid, which carries the same status bar.
"""
from __future__ import annotations

import webbrowser
from dataclasses import dataclass, replace as dataclass_replace

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import (
    Action,
    AvatarWidget,
    CaptionLabel,
    FluentIcon,
    InfoBar,
    InfoBarPosition,
    PushButton,
    RoundMenu,
    SubtitleLabel,
)

from ..config import app_config
from ..core.home_feed import MODE_AUTHOR, FeedSection, FeedTab
from ..core.manager import download_manager
from ..core.rating import RATING_ALL, UI_RATING_KEY, normalize_rating
from ..core.rules import active_rule_id
from ..core.search import IWARA_IMAGE_SOURCE_KIND, SearchVideo, avatar_url
from ..i18n import tr
from ..signal_bus import signal_bus
from .author_status import AuthorStatusBar, clean_username, local_chip_state, web_chip_state
from .home_page import BrowseState, BrowseView, queueable_ids
from .media_card import read_pixmap
from .home_workers import CoverFetcher
from .theme import PAGE_MARGINS, PAGE_SPACING, set_secondary_text


@dataclass
class AuthorViewState:
    target: tuple[str, str, str, str]
    browse: BrowseState | None = None


class AuthorView(QWidget):
    """Works of one author with the status bar; for authors not subscribed locally."""

    back_requested = Signal()
    open_subscription_requested = Signal(int)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._fetcher = CoverFetcher(self)
        self._username = ""
        self._target: tuple[str, str, str, str] = ("", "", "", "")

        root = QVBoxLayout(self)
        root.setContentsMargins(*PAGE_MARGINS)
        root.setSpacing(PAGE_SPACING)

        head = QHBoxLayout()
        head.setSpacing(12)
        back = PushButton(tr("Back", "返回", "戻る"), self, FluentIcon.LEFT_ARROW)
        back.clicked.connect(self.back_requested)
        head.addWidget(back)
        self._avatar = AvatarWidget(self)
        self._avatar.setRadius(26)
        head.addWidget(self._avatar)
        names = QVBoxLayout()
        names.setSpacing(0)
        self._name = SubtitleLabel("", self)
        self._handle = CaptionLabel("", self)
        set_secondary_text(self._handle)
        names.addWidget(self._name)
        names.addWidget(self._handle)
        head.addLayout(names, 1)
        search_btn = PushButton(tr("Open in Search", "在搜索页打开", "検索ページで開く"), self, FluentIcon.SEARCH)
        search_btn.clicked.connect(self._open_in_search)
        head.addWidget(search_btn)
        site_btn = PushButton(tr("Open on Iwara", "在 Iwara 打开", "Iwaraで開く"), self, FluentIcon.GLOBE)
        site_btn.clicked.connect(self._open_on_site)
        head.addWidget(site_btn)
        root.addLayout(head)

        self.status = AuthorStatusBar(self)
        self.status.profile_loaded.connect(self._on_profile)
        self.status.open_subscription_requested.connect(self.open_subscription_requested)
        root.addWidget(self.status)

        self._browse = BrowseView(self._fetcher, self)
        self._browse.set_embedded(True)
        self._browse.open_requested.connect(self._open_post)
        self._browse.context_requested.connect(self._show_menu)
        self._browse.queue_requested.connect(self._queue)
        root.addWidget(self._browse, 1)
        self._fetcher.cover_ready.connect(self._on_cover)

    # ── content ──────────────────────────────────────────────────────────────

    @property
    def username(self) -> str:
        return self._username

    def open_author(self, target: tuple[str, str, str, str]):
        self._show_author(target)
        rating = normalize_rating(app_config.get_ui_value(UI_RATING_KEY, RATING_ALL))
        self._browse.open(self._section(), "videos", rating)

    def _section(self) -> FeedSection:
        username, name = self._username, self._target[1]
        return FeedSection(
            "author",
            name or username,
            (
                FeedTab("videos", tr("Videos", "视频", "動画"), "video", (("sort", "date"),), mode=MODE_AUTHOR, value=username),
                FeedTab("images", tr("Images", "图片", "画像"), "image", (("sort", "date"),), mode=MODE_AUTHOR, value=username),
            ),
        )

    def view_state(self) -> AuthorViewState:
        return AuthorViewState(self._target, self._browse.view_state())

    def restore_state(self, state: AuthorViewState):
        """Show a recorded author again: same tab, page, cards, selection and scroll."""

        if state.browse is None:
            self.open_author(state.target)
            return
        self._show_author(state.target)
        self._browse.restore_state(dataclass_replace(state.browse, section=self._section()))

    def _show_author(self, target: tuple[str, str, str, str]):
        username, name, user_id, avatar = (list(target) + ["", "", "", ""])[:4]
        self._target = (str(username), str(name), str(user_id), str(avatar))
        self._username = clean_username(username)
        self._name.setText(name or self._username)
        self._handle.setText(f"@{self._username}")
        self._avatar.setText((name or self._username)[:1].upper())
        if avatar:
            self._fetcher.request([("avatar", self._username, avatar)])
            cached = self._fetcher.path_for("avatar", self._username)
            if cached:
                self._on_cover("avatar", self._username, cached)
        self.status.set_author(self._username, name, user_id, avatar)

    def refresh_status(self):
        self.status.refresh_local()

    def _on_profile(self, user: dict):
        name = str(user.get("name") or "").strip()
        if name:
            self._name.setText(name)
        avatar = avatar_url(user)
        if avatar:
            self._fetcher.request([("avatar", self._username, avatar)])

    def _on_cover(self, kind: str, key: str, path: str):
        if kind == "avatar" and key == self._username:
            pixmap = read_pixmap(path, 256)
            if not pixmap.isNull():
                self._avatar.setImage(pixmap)

    # ── actions ──────────────────────────────────────────────────────────────

    def _open_in_search(self):
        if self._username:
            signal_bus.search_requested.emit({"author": (self._username, self._name.text(), self.status._user_id, "")})

    def _open_on_site(self):
        if self._username:
            webbrowser.open(f"https://www.iwara.tv/profile/{self._username}")

    def _open_post(self, video: SearchVideo):
        kind = "image" if video.source_kind == IWARA_IMAGE_SOURCE_KIND else "video"
        signal_bus.media_detail_requested.emit(kind, video.video_id)

    def _queue(self, videos: list, rule_id: str = ""):
        ids = queueable_ids([v for v in videos if isinstance(v, SearchVideo)])
        if not ids:
            InfoBar.warning(
                title=tr("Nothing to download", "没有可下载的内容", "ダウンロード対象がありません"),
                content=tr("Image posts and embedded videos cannot be queued.", "图片作品和外站嵌入视频无法加入下载队列。", "画像投稿と埋め込み動画はキューに追加できません。"),
                orient=Qt.Orientation.Horizontal, isClosable=True, position=InfoBarPosition.TOP, duration=3500, parent=self.window(),
            )
            return
        accepted = download_manager.enqueue_video_ids(
            ids, source_label=f"@{self._username}", rule_id=rule_id or active_rule_id(),
        )
        InfoBar.success(
            title=tr("Added to queue", "已加入队列", "キューに追加"),
            content=tr(f"Accepted {accepted} video(s)", f"已接受 {accepted} 个视频", f"{accepted} 件を追加しました"),
            orient=Qt.Orientation.Horizontal, isClosable=True, position=InfoBarPosition.TOP, duration=3000, parent=self.window(),
        )

    def _show_menu(self, video: SearchVideo, global_pos: QPoint):
        menu = RoundMenu(parent=self)
        menu.addAction(Action(FluentIcon.VIEW, tr("View details", "查看详情", "詳細を表示"), self, triggered=lambda: self._open_post(video)))
        if video.source_kind != IWARA_IMAGE_SOURCE_KIND and video.downloadable:
            menu.addAction(Action(
                FluentIcon.PLAY, tr("Play", "播放", "再生"), self,
                triggered=lambda: signal_bus.video_preview_requested.emit(video.video_id, video.title, ""),
            ))
            menu.addAction(Action(FluentIcon.DOWNLOAD, tr("Add to download queue", "加入下载队列", "ダウンロードキューに追加"), self, triggered=lambda: self._queue([video])))
        menu.addAction(Action(FluentIcon.GLOBE, tr("Open in browser", "在浏览器打开", "ブラウザーで開く"), self, triggered=lambda: webbrowser.open(video.source_url)))
        menu.exec(global_pos)

    def shutdown(self, timeout_ms: int = 30_000) -> bool:
        return all([
            self.status.shutdown(timeout_ms),
            self._browse.shutdown(timeout_ms),
            self._fetcher.shutdown(timeout_ms),
        ])


__all__ = ["AuthorStatusBar", "AuthorView", "clean_username", "local_chip_state", "web_chip_state"]
