"""Home: account feed, hot videos and hot images, with in-app detail pages.

The page is a small stack — the feed, a paginated "More" view and a post
detail view.  Each move records where the user was in the window's shared back
history (``navigation``), so Back returns to exactly that list or post, while
the batch download tools stay one click away (select cards, pick a rule, queue).
"""
from __future__ import annotations

import webbrowser
from dataclasses import dataclass, replace as dataclass_replace

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtWidgets import QStackedWidget, QVBoxLayout, QWidget

from qfluentwidgets import Action, FluentIcon, InfoBar, InfoBarPosition, RoundMenu

from ...config import app_config
from ...core.home_feed import MODE_AUTHOR
from ...core.manager import download_manager
from ...core.rating import RATING_ALL, UI_RATING_KEY, normalize_rating
from ...core.rules import active_rule_id
from ...core.search.models import IWARA_IMAGE_SOURCE_KIND, SearchVideo
from ...core.search.normalize import avatar_url
from ...i18n import tr
from ...signal_bus import signal_bus
from .workers import CoverFetcher
from ..media.detail_state import DetailState
from ..media.detail_view import DetailView
from ..navigation import navigate_back, record_navigation, restore_scroll
from .browse_view import BrowseState, BrowseView
from .feed_view import HomeFeedView
from .section_block import queueable_ids
from ..downloads.queue_preview import confirm_videos


@dataclass
class HomeState:
    view: str  # "feed" | "browse" | "detail"
    feed_scroll: int = 0
    browse: BrowseState | None = None
    detail: DetailState | None = None


class HomeInterface(QWidget):
    """The Home page: feed → browse → detail, sharing the window's back history."""

    open_settings_requested = Signal()

    _FEED, _BROWSE, _DETAIL = 0, 1, 2

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("HomeInterface")
        self._fetcher = CoverFetcher(self)
        self._loaded_once = False
        self._rating = normalize_rating(app_config.get_ui_value(UI_RATING_KEY, RATING_ALL))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._stack = QStackedWidget(self)
        layout.addWidget(self._stack)

        self._feed = HomeFeedView(self._fetcher, self)
        self._browse = BrowseView(self._fetcher, self)
        self._detail = DetailView(self._fetcher, self)
        for view in (self._feed, self._browse, self._detail):
            self._stack.addWidget(view)
        self._feed.set_rating(self._rating)
        self._browse.set_rating(self._rating)

        self._feed.rating_selected.connect(self._choose_rating)
        self._feed.more_requested.connect(self._open_browse)
        self._feed.customize_requested.connect(self.customize)
        self._feed.settings_requested.connect(self.open_settings_requested)
        for view in (self._feed, self._browse):
            view.open_requested.connect(self._open_video)
            view.context_requested.connect(self._show_menu)
            view.queue_requested.connect(self._queue)
        self._detail.open_requested.connect(self._open_video)
        self._detail.context_requested.connect(self._show_menu)
        self._detail.back_requested.connect(self._back)
        self._browse.back_requested.connect(self._back)

        signal_bus.content_rating_changed.connect(self._on_rating_broadcast)
        signal_bus.login_state_changed.connect(self._on_login_changed)
        signal_bus.home_layout_changed.connect(self._on_layout_changed)

    # ── lifecycle ────────────────────────────────────────────────────────────

    def showEvent(self, event):
        super().showEvent(event)
        # Rows paint from the cache and only re-check the site once stale, so
        # coming back to Home costs nothing.
        if not self._loaded_once:
            self._loaded_once = True
            QTimer.singleShot(0, self._feed.reload_all)
        else:
            self._feed.reload_all()

    def shutdown(self, *, timeout_ms: int = 30_000) -> bool:
        results = [
            self._feed.shutdown(timeout_ms),
            self._browse.shutdown(timeout_ms),
            self._detail.shutdown(timeout_ms),
            self._fetcher.shutdown(timeout_ms),
        ]
        return all(results)

    def refresh_theme_styles(self):
        self._feed.update()
        for widget in self.findChildren(QWidget):
            widget.update()

    # ── rating / login ───────────────────────────────────────────────────────

    def _choose_rating(self, rating: str):
        rating = normalize_rating(rating)
        if rating == self._rating:
            return
        app_config.set_ui_value(UI_RATING_KEY, rating)
        self._apply_rating(rating)
        signal_bus.content_rating_changed.emit(rating)

    def _on_rating_broadcast(self, rating: str):
        rating = normalize_rating(rating)
        if rating != self._rating:
            self._apply_rating(rating)

    def _apply_rating(self, rating: str):
        self._rating = rating
        self._feed.set_rating(rating)
        self._browse.set_rating(rating)
        if self._loaded_once:
            if self.isVisible():
                self._feed.reload_all()
            else:
                self._feed.invalidate()

    def _on_login_changed(self, _logged_in: bool):
        if not self._loaded_once:
            return
        if self.isVisible():
            self._feed.reload_subscriptions()
        else:
            self._feed.invalidate(subscriptions_only=True)

    # ── layout ───────────────────────────────────────────────────────────────

    def customize(self):
        """Open the editor for which rows Home shows."""

        from .layout_dialog import HomeLayoutDialog

        HomeLayoutDialog.edit(self.window())

    def _on_layout_changed(self):
        self._feed.rebuild()
        self._feed.set_rating(self._rating)
        self._browse.set_rating(self._rating)
        if self._loaded_once and self.isVisible():
            self._feed.reload_all()

    # ── navigation ───────────────────────────────────────────────────────────

    def nav_snapshot(self) -> HomeState:
        index = self._stack.currentIndex()
        if index == self._DETAIL:
            return HomeState("detail", detail=self._detail.view_state())
        if index == self._BROWSE:
            return HomeState("browse", browse=self._browse.view_state())
        return HomeState("feed", feed_scroll=self._feed._scroll.verticalScrollBar().value())

    def nav_restore(self, state: HomeState):
        if state.view == "detail" and state.detail is not None:
            if self._stack.currentIndex() != self._DETAIL or self._detail.item != (state.detail.kind, state.detail.item_id):
                self._detail.restore_state(state.detail)
            else:
                restore_scroll(self._detail._scroll, state.detail.scroll)
            self._stack.setCurrentIndex(self._DETAIL)
        elif state.view == "browse" and state.browse is not None:
            browse = state.browse
            # The row's own section, so its labels follow the current language.
            fresh = next((b.section for b in self._feed.blocks if b.section.id == browse.section.id), None)
            if fresh is not None:
                browse = dataclass_replace(browse, section=fresh)
            self._browse.restore_state(browse)
            self._stack.setCurrentIndex(self._BROWSE)
        else:
            self._stack.setCurrentIndex(self._FEED)
            restore_scroll(self._feed._scroll, state.feed_scroll)

    def refresh(self):
        """Reload the feeds, or the open "More" list."""

        if self._stack.currentIndex() == self._BROWSE:
            self._browse.reload()
        elif self._stack.currentIndex() == self._FEED:
            self._feed.reload_all(force=True)

    def go_back(self):
        """Back through the history; with none left, up from a post or list to the feed."""

        self._back()

    def dismiss_transient(self) -> bool:
        """Esc on the page: first drop the "More" list's selection, if there is one."""

        if self._stack.currentIndex() == self._BROWSE and self._browse._grid.selected_videos():
            self._browse._grid.clear_selection()
            return True
        return False

    def close_current(self):
        """Leave an open post or "More" list, doing nothing on the feed itself."""

        if self._stack.currentIndex() != self._FEED:
            self._back()

    def scroll_to(self, *, bottom: bool):
        area = {
            self._FEED: self._feed._scroll,
            self._BROWSE: self._browse._scroll,
            self._DETAIL: self._detail._scroll,
        }.get(self._stack.currentIndex())
        if area is not None:
            bar = area.verticalScrollBar()
            bar.setValue(bar.maximum() if bottom else bar.minimum())

    def fold_all(self, folded: bool):
        if self._stack.currentIndex() == self._FEED:
            for block in self._feed.blocks:
                block.set_folded(folded)

    _DETAIL_ACTIONS = {
        "like": "_toggle_like",
        "play": "_play",
        "download": "_queue",
        "browser": "_open_in_browser",
        "copy": "_copy_link",
        "author": "_author_page",
    }

    def detail_action(self, name: str):
        """Run a post action (like, play, …) from the keyboard; only while a post is open."""

        method = self._DETAIL_ACTIONS.get(name)
        if method and self._stack.currentIndex() == self._DETAIL:
            getattr(self._detail, method)()

    def _back(self):
        if not navigate_back(self) and self._stack.currentIndex() != self._FEED:
            self._stack.setCurrentIndex(self._FEED)

    def _open_browse(self, section_id: str, tab_id: str):
        section = next((b.section for b in self._feed.blocks if b.section.id == section_id), None)
        if section is None:
            return
        tab = section.tab(tab_id)
        if tab.mode == MODE_AUTHOR:
            target = (tab.value, tab.value, "", "")
            signal_bus.author_page_requested.emit(target)
            return
        if tab.opens_in_search:
            # Public rankings are the search page's job: it already pages,
            # sorts, switches view and queues downloads.
            signal_bus.search_requested.emit(tab.search_request())
            return
        record_navigation(self)
        self._browse.open(section, tab_id, self._rating)
        self._stack.setCurrentIndex(self._BROWSE)

    def _open_video(self, video: SearchVideo):
        kind = "image" if video.source_kind == IWARA_IMAGE_SOURCE_KIND else "video"
        self.show_detail(kind, video.video_id, video)

    def show_detail(
        self,
        kind: str,
        item_id: str,
        preview: SearchVideo | None = None,
        *,
        external: bool = False,
    ):
        """Open a post's detail page (also used by other pages).

        ``external`` means another page asked for it and the window has already
        recorded where the user came from; otherwise this page records itself.
        """

        kind = "image" if kind == "image" else "video"
        if self._stack.currentIndex() == self._DETAIL and self._detail.item == (kind, str(item_id)):
            return  # already showing it
        if not external:
            record_navigation(self)
        self._detail.load(kind, str(item_id), preview)
        self._stack.setCurrentIndex(self._DETAIL)

    # ── actions ──────────────────────────────────────────────────────────────

    def _queue(self, videos: list, rule_id: str = ""):
        ids = queueable_ids([v for v in videos if isinstance(v, SearchVideo)])
        if not ids:
            InfoBar.warning(
                title=tr("Nothing to download", "没有可下载的内容", "ダウンロード対象がありません"),
                content=tr("Image posts and embedded videos cannot be queued.", "图片作品和外站嵌入视频无法加入下载队列。", "画像投稿と埋め込み動画はキューに追加できません。"),
                orient=Qt.Orientation.Horizontal, isClosable=True, position=InfoBarPosition.TOP, duration=3500, parent=self.window(),
            )
            return
        # Confirm first: counts, where the files go, and the rule.
        wanted = set(ids)
        confirm_videos(
            self,
            [v for v in videos if isinstance(v, SearchVideo) and v.video_id in wanted],
            label=tr("Home", "首页", "ホーム"),
            rule_id=rule_id or active_rule_id(),
        )

    def _author_target(self, video: SearchVideo) -> tuple[str, str, str, str] | None:
        user = video.raw.get("user") if isinstance(video.raw, dict) else None
        user = user if isinstance(user, dict) else {}
        username = str(user.get("username") or video.author_username or "").strip()
        if not username:
            return None
        return username, str(user.get("name") or video.author_name or username), str(user.get("id") or ""), avatar_url(user)

    def _show_menu(self, video: SearchVideo, global_pos: QPoint):
        menu = RoundMenu(parent=self)
        is_image = video.source_kind == IWARA_IMAGE_SOURCE_KIND
        menu.addAction(Action(FluentIcon.VIEW, tr("View details", "查看详情", "詳細を表示"), self, triggered=lambda: self._open_video(video)))
        if not is_image and video.downloadable:
            menu.addAction(Action(
                FluentIcon.PLAY, tr("Play", "播放", "再生"), self,
                triggered=lambda: signal_bus.video_preview_requested.emit(video.video_id, video.title, ""),
            ))
            menu.addAction(Action(FluentIcon.DOWNLOAD, tr("Add to download queue", "加入下载队列", "ダウンロードキューに追加"), self, triggered=lambda: self._queue([video])))
        menu.addAction(Action(
            FluentIcon.GLOBE, tr("Open in browser", "在浏览器打开", "ブラウザーで開く"), self,
            triggered=lambda: webbrowser.open(video.source_url),
        ))
        target = self._author_target(video)
        if target:
            menu.addSeparator()
            menu.addAction(Action(
                FluentIcon.PEOPLE, tr("Open author page", "打开作者页", "作者ページを開く"), self,
                triggered=lambda: signal_bus.author_page_requested.emit(target),
            ))
            menu.addAction(Action(
                FluentIcon.SEARCH, tr("View author's works", "查看作者作品", "作者の作品を表示"), self,
                triggered=lambda: signal_bus.search_requested.emit({"author": target}),
            ))
            menu.addAction(Action(FluentIcon.PEOPLE, tr("Subscribe to author", "加入订阅", "作者を購読"), self, triggered=lambda: self._subscribe(target)))
        menu.exec(global_pos)

    def _subscribe(self, target: tuple[str, str, str, str]):
        username, title, remote_id, avatar = target
        try:
            source_id = int(download_manager.add_author_subscription(
                username, title=title, remote_id=remote_id, avatar_url=avatar,
            ) or 0)
        except Exception as exc:
            InfoBar.error(
                title=tr("Subscription failed", "订阅失败", "購読に失敗"), content=str(exc),
                orient=Qt.Orientation.Horizontal, isClosable=True, position=InfoBarPosition.TOP, duration=4000, parent=self.window(),
            )
            return
        if source_id:
            signal_bus.subscription_source_added.emit(source_id)
            InfoBar.success(
                title=tr("Author added", "作者已加入订阅", "作者を購読に追加しました"),
                content=tr(f"@{username} is now in your local subscriptions", f"@{username} 已加入本地订阅", f"@{username} をローカル購読に追加しました"),
                orient=Qt.Orientation.Horizontal, isClosable=True, position=InfoBarPosition.TOP, duration=3500, parent=self.window(),
            )
