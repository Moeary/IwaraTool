"""Home: account feed, hot videos and hot images, with in-app detail pages.

The page is a small stack — the feed, a paginated "More" view and a post
detail view — with a back trail, so browsing feels like the website while the
batch download tools stay one click away (select cards, pick a rule, queue).
"""
from __future__ import annotations

import time
import webbrowser

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from qfluentwidgets import (
    Action,
    BodyLabel,
    CaptionLabel,
    FluentIcon,
    HyperlinkButton,
    InfoBar,
    InfoBarPosition,
    PrimaryPushButton,
    PushButton,
    RoundMenu,
    SegmentedWidget,
    SubtitleLabel,
    TitleLabel,
    ToolButton,
)

from ..config import app_config
from ..core.home_feed import (
    BROWSE_PAGE_LIMIT,
    HOME_PAGE_LIMIT,
    FeedSection,
    home_sections,
)
from ..core.manager import download_manager
from ..core.rating import RATING_ALL, UI_RATING_KEY, normalize_rating, rating_options
from ..core.rules import active_rule_id
from ..core.search import IWARA_IMAGE_SOURCE_KIND, SearchVideo, avatar_url
from ..i18n import tr
from ..signal_bus import signal_bus
from .home_workers import CoverFetcher, FeedResult, FeedWorker, stop_workers
from .media_card import CardSizeControl, MediaGrid, transparent_scroll_area
from .media_detail import DetailView
from .rules_page import RulePicker
from .theme import PAGE_MARGINS, PAGE_SPACING, set_secondary_text

STALE_AFTER_SECONDS = 10 * 60


def queueable_ids(videos: list[SearchVideo]) -> list[str]:
    """Iwara video ids that can be downloaded (image posts and embeds cannot)."""

    seen: set[str] = set()
    ids: list[str] = []
    for video in videos:
        if video.source_kind == "iwara" and video.downloadable and video.video_id not in seen:
            seen.add(video.video_id)
            ids.append(video.video_id)
    return ids


class SectionBlock(QWidget):
    """One titled row of cards on the feed."""

    more_requested = Signal(str, str)  # section id, tab id
    queue_requested = Signal(list, str)
    open_requested = Signal(object)
    context_requested = Signal(object, QPoint)
    settings_requested = Signal()

    def __init__(self, section: FeedSection, fetcher: CoverFetcher, parent: QWidget | None = None):
        super().__init__(parent)
        self.section = section
        self._rating = RATING_ALL
        self._token = 0
        self._worker: FeedWorker | None = None
        self._workers: list[FeedWorker] = []  # includes superseded, still-running loads
        self._loaded_at = 0.0
        saved = str(app_config.get_ui_value(f"home_tab_{section.id}_v1", "") or "")
        self._tab_id = section.tab(saved).id

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        header = QHBoxLayout()
        header.setSpacing(12)
        header.addWidget(SubtitleLabel(section.title, self))
        self._tabs = SegmentedWidget(self)
        for tab in section.tabs:
            self._tabs.addItem(tab.id, tab.label, lambda _=False, tab_id=tab.id: self._select_tab(tab_id))
        self._tabs.setCurrentItem(self._tab_id)
        self._tabs.setVisible(len(section.tabs) > 1)
        header.addWidget(self._tabs)
        header.addStretch(1)
        self._queue_btn = PushButton(tr("Download shown", "下载当前展示", "表示中をダウンロード"), self, FluentIcon.DOWNLOAD)
        self._queue_btn.setToolTip(tr("Queue every video shown in this row", "把这一行展示的视频全部加入下载队列", "この行の動画をすべてキューに追加"))
        self._queue_btn.clicked.connect(self._queue_shown)
        header.addWidget(self._queue_btn)
        self._refresh_btn = ToolButton(FluentIcon.SYNC, self)
        self._refresh_btn.setToolTip(tr("Refresh", "刷新", "更新"))
        self._refresh_btn.clicked.connect(lambda: self.reload(force=True))
        header.addWidget(self._refresh_btn)
        # Public rankings open in the Search page; only the account feed has a page of its own.
        public = not section.tabs[0].needs_login
        self._more_btn = HyperlinkButton(
            "",
            tr("More in Search ›", "在搜索页查看更多 ›", "検索ページで見る ›") if public else tr("More ›", "查看更多 ›", "もっと見る ›"),
            self,
        )
        self._more_btn.clicked.connect(lambda: self.more_requested.emit(self.section.id, self._tab_id))
        header.addWidget(self._more_btn)
        root.addLayout(header)

        self._state = BodyLabel("", self)
        self._state.setWordWrap(True)
        set_secondary_text(self._state)
        root.addWidget(self._state)
        self._login_btn = PrimaryPushButton(tr("Open Settings to sign in", "前往设置登录", "設定でログイン"), self, FluentIcon.SETTING)
        self._login_btn.clicked.connect(self.settings_requested)
        root.addWidget(self._login_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self._login_btn.hide()

        self._grid = MediaGrid(self, selectable=True, max_rows=2, resizable=True)
        self._grid.bind_fetcher(fetcher)
        self._grid.card_activated.connect(self.open_requested)
        self._grid.card_context_requested.connect(self.context_requested)
        self._grid.selection_changed.connect(self._sync_buttons)
        root.addWidget(self._grid)
        self._sync_buttons()

    # ── state ────────────────────────────────────────────────────────────────

    @property
    def tab(self):
        return self.section.tab(self._tab_id)

    def _select_tab(self, tab_id: str):
        if tab_id == self._tab_id:
            return
        self._tab_id = tab_id
        app_config.set_ui_value(f"home_tab_{self.section.id}_v1", tab_id)
        self.reload(force=True)

    def set_rating(self, rating: str):
        self._rating = normalize_rating(rating)

    def invalidate(self):
        self._loaded_at = 0.0

    def is_stale(self) -> bool:
        return not self._loaded_at or time.monotonic() - self._loaded_at > STALE_AFTER_SECONDS

    def _sync_buttons(self):
        selected = self._grid.selected_videos()
        shown = self._grid.shown_videos()
        targets = selected or shown
        count = len(queueable_ids(targets))
        self._queue_btn.setEnabled(count > 0)
        if selected:
            self._queue_btn.setText(tr(f"Download selected ({count})", f"下载选中（{count}）", f"選択をダウンロード（{count}）"))
        else:
            self._queue_btn.setText(tr("Download shown", "下载当前展示", "表示中をダウンロード"))
        self._queue_btn.setVisible(self.tab.kind == "video")

    def _queue_shown(self):
        videos = self._grid.selected_videos() or self._grid.shown_videos()
        self.queue_requested.emit(videos, "")

    # ── loading ──────────────────────────────────────────────────────────────

    def reload(self, *, force: bool = False):
        tab = self.tab
        self._sync_buttons()
        self._grid.set_videos([])
        self._login_btn.hide()
        if tab.needs_login and not download_manager.is_logged_in():
            self._state.setText(tr(
                "Sign in to see the newest uploads from the creators you follow on Iwara.",
                "登录 Iwara 账号后，这里会显示你关注作者的最新作品。",
                "Iwaraにログインすると、フォロー中の作者の最新作がここに表示されます。",
            ))
            self._state.show()
            self._login_btn.show()
            self._loaded_at = 0.0
            return
        self._token += 1
        if self._worker is not None:
            self._worker.requestInterruption()
        self._state.setText(tr("Loading…", "加载中…", "読み込み中…"))
        self._state.show()
        worker = FeedWorker(self._token, tab.kind, tab.request_params(self._rating), 0, HOME_PAGE_LIMIT)
        worker.result_ready.connect(self._on_result)
        worker.finished.connect(lambda worker=worker: self._on_finished(worker))
        self._worker = worker
        self._workers.append(worker)
        worker.start()

    def _on_finished(self, worker: FeedWorker):
        if self._worker is worker:
            self._worker = None
        if worker in self._workers:
            self._workers.remove(worker)
        worker.deleteLater()

    def _on_result(self, result: FeedResult):
        if result.token != self._token:
            return
        self._loaded_at = time.monotonic()
        if result.error and not result.items:
            self._state.setText(tr(f"Could not load: {result.error}", f"加载失败：{result.error}", f"読み込めませんでした: {result.error}"))
            self._loaded_at = 0.0
        elif not result.items:
            self._state.setText(tr("Nothing here yet.", "这里暂时没有内容。", "まだ何もありません。"))
        else:
            self._state.hide()
        self._grid.set_videos(result.items)
        self._sync_buttons()

    def shutdown(self, timeout_ms: int) -> bool:
        return stop_workers(list(self._workers), timeout_ms)


class HomeFeedView(QWidget):
    """Header (rating selector, refresh) over the scrolling sections."""

    more_requested = Signal(str, str)
    queue_requested = Signal(list, str)
    open_requested = Signal(object)
    context_requested = Signal(object, QPoint)
    settings_requested = Signal()
    rating_selected = Signal(str)

    def __init__(self, fetcher: CoverFetcher, parent: QWidget | None = None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(*PAGE_MARGINS)
        root.setSpacing(PAGE_SPACING)

        header = QHBoxLayout()
        header.setSpacing(14)
        header.addWidget(TitleLabel(tr("Home", "首页", "ホーム"), self))
        header.addStretch(1)
        header.addWidget(CardSizeControl(self))
        header.addWidget(BodyLabel(tr("Content", "内容分级", "コンテンツ"), self))
        self._rating = SegmentedWidget(self)
        for label, value in rating_options():
            self._rating.addItem(value, label, lambda _=False, value=value: self.rating_selected.emit(value))
        header.addWidget(self._rating)
        self._refresh_all_btn = PushButton(tr("Refresh all", "全部刷新", "すべて更新"), self, FluentIcon.SYNC)
        self._refresh_all_btn.clicked.connect(lambda: self.reload_all(force=True))
        header.addWidget(self._refresh_all_btn)
        root.addLayout(header)

        self._scroll = transparent_scroll_area("HomeFeedScroll", self)
        page = QWidget()
        column = QVBoxLayout(page)
        column.setContentsMargins(0, 0, 8, 24)
        column.setSpacing(22)
        self.blocks: list[SectionBlock] = []
        for section in home_sections():
            block = SectionBlock(section, fetcher, page)
            block.more_requested.connect(self.more_requested)
            block.queue_requested.connect(self.queue_requested)
            block.open_requested.connect(self.open_requested)
            block.context_requested.connect(self.context_requested)
            block.settings_requested.connect(self.settings_requested)
            column.addWidget(block)
            self.blocks.append(block)
        column.addStretch(1)
        self._scroll.setWidget(page)
        root.addWidget(self._scroll, 1)

    def set_rating(self, rating: str):
        rating = normalize_rating(rating)
        self._rating.setCurrentItem(rating)
        for block in self.blocks:
            block.set_rating(rating)

    def reload_all(self, *, force: bool = False):
        for block in self.blocks:
            if force or block.is_stale():
                block.reload(force=force)

    def reload_subscriptions(self):
        for block in self.blocks:
            if block.tab.needs_login:
                block.reload(force=True)

    def invalidate(self, *, subscriptions_only: bool = False):
        """Mark rows stale so the next time Home is shown they reload."""

        for block in self.blocks:
            if not subscriptions_only or block.tab.needs_login:
                block.invalidate()

    def shutdown(self, timeout_ms: int) -> bool:
        return all([block.shutdown(timeout_ms) for block in self.blocks])


class BrowseView(QWidget):
    """A section's full, paginated list with multi-select downloading."""

    back_requested = Signal()
    open_requested = Signal(object)
    context_requested = Signal(object, QPoint)
    queue_requested = Signal(list, str)

    def __init__(self, fetcher: CoverFetcher, parent: QWidget | None = None):
        super().__init__(parent)
        self._section: FeedSection | None = None
        self._tab_id = ""
        self._rating = RATING_ALL
        self._page = 0
        self._has_more = False
        self._token = 0
        self._worker: FeedWorker | None = None
        self._workers: list[FeedWorker] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(*PAGE_MARGINS)
        root.setSpacing(PAGE_SPACING)

        bar = QHBoxLayout()
        self._bar = bar
        bar.setSpacing(12)
        back = PushButton(tr("Back", "返回", "戻る"), self, FluentIcon.LEFT_ARROW)
        back.clicked.connect(self.back_requested)
        bar.addWidget(back)
        self._title = SubtitleLabel("", self)
        bar.addWidget(self._title)
        self._tabs = SegmentedWidget(self)
        bar.addWidget(self._tabs)
        bar.addStretch(1)
        self._prev_btn = ToolButton(FluentIcon.LEFT_ARROW, self)
        self._prev_btn.clicked.connect(lambda: self._go(self._page - 1))
        self._page_label = BodyLabel("", self)
        self._page_label.setMinimumWidth(72)
        self._page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._next_btn = ToolButton(FluentIcon.RIGHT_ARROW, self)
        self._next_btn.clicked.connect(lambda: self._go(self._page + 1))
        for widget in (self._prev_btn, self._page_label, self._next_btn):
            bar.addWidget(widget)
        root.addLayout(bar)

        tools = QHBoxLayout()
        tools.setSpacing(8)
        self._select_all_btn = PushButton(tr("Select all", "全选", "すべて選択"), self)
        self._select_all_btn.clicked.connect(lambda: self._grid.select_all(True))
        self._clear_btn = PushButton(tr("Clear", "取消选择", "選択解除"), self)
        self._clear_btn.clicked.connect(lambda: self._grid.clear_selection())
        tools.addWidget(self._select_all_btn)
        tools.addWidget(self._clear_btn)
        self._selected_label = CaptionLabel("", self)
        set_secondary_text(self._selected_label)
        tools.addWidget(self._selected_label)
        tools.addStretch(1)
        tools.addWidget(CardSizeControl(self))
        tools.addWidget(CaptionLabel(tr("Download rule", "下载规则", "ダウンロードルール"), self))
        self._rule_picker = RulePicker(self)
        tools.addWidget(self._rule_picker)
        self._queue_btn = PrimaryPushButton(tr("Add selected", "加入选中项", "選択を追加"), self, FluentIcon.DOWNLOAD)
        self._queue_btn.clicked.connect(self._queue_selected)
        tools.addWidget(self._queue_btn)
        root.addLayout(tools)

        self._state = CaptionLabel("", self)
        set_secondary_text(self._state)
        root.addWidget(self._state)

        self._scroll = transparent_scroll_area("HomeBrowseScroll", self)
        page = QWidget()
        column = QVBoxLayout(page)
        column.setContentsMargins(0, 0, 8, 24)
        column.setSpacing(12)
        self._grid = MediaGrid(page, selectable=True, resizable=True)
        self._grid.bind_fetcher(fetcher)
        self._grid.card_activated.connect(self.open_requested)
        self._grid.card_context_requested.connect(self.context_requested)
        self._grid.selection_changed.connect(self._sync_selection)
        column.addWidget(self._grid)
        footer = QHBoxLayout()
        footer.addStretch(1)
        self._prev_bottom = PushButton(tr("Previous page", "上一页", "前のページ"), page, FluentIcon.LEFT_ARROW)
        self._prev_bottom.clicked.connect(lambda: self._go(self._page - 1))
        self._next_bottom = PushButton(tr("Next page", "下一页", "次のページ"), page, FluentIcon.RIGHT_ARROW)
        self._next_bottom.clicked.connect(lambda: self._go(self._page + 1))
        footer.addWidget(self._prev_bottom)
        footer.addWidget(self._next_bottom)
        footer.addStretch(1)
        column.addLayout(footer)
        column.addStretch(1)
        self._scroll.setWidget(page)
        root.addWidget(self._scroll, 1)
        self._sync_selection()

    def open(self, section: FeedSection, tab_id: str, rating: str):
        self._section = section
        self._tab_id = section.tab(tab_id).id
        self._rating = normalize_rating(rating)
        self._title.setText(section.title)
        self._rebuild_tabs()
        self._go(0)

    def _rebuild_tabs(self):
        # SegmentedWidget has no clear(); swap in a fresh one for the new section.
        assert self._section is not None
        old = self._tabs
        self._tabs = SegmentedWidget(self)
        for tab in self._section.tabs:
            self._tabs.addItem(tab.id, tab.label, lambda _=False, tab_id=tab.id: self._pick_tab(tab_id))
        self._tabs.setCurrentItem(self._tab_id)
        self._tabs.setVisible(len(self._section.tabs) > 1)
        position = self._bar.indexOf(old)
        self._bar.removeWidget(old)
        old.hide()
        old.deleteLater()
        self._bar.insertWidget(position, self._tabs)

    def reload(self):
        self._go(self._page)

    def _pick_tab(self, tab_id: str):
        if tab_id != self._tab_id:
            self._tab_id = tab_id
            self._go(0)

    def set_rating(self, rating: str):
        rating = normalize_rating(rating)
        if rating != self._rating:
            self._rating = rating
            if self._section is not None:
                self._go(0)

    def _go(self, page: int):
        if self._section is None:
            return
        self._page = max(0, page)
        self._token += 1
        if self._worker is not None:
            self._worker.requestInterruption()
        tab = self._section.tab(self._tab_id)
        self._state.setText(tr("Loading…", "加载中…", "読み込み中…"))
        self._grid.set_videos([])
        self._update_pager()
        worker = FeedWorker(self._token, tab.kind, tab.request_params(self._rating), self._page, BROWSE_PAGE_LIMIT)
        worker.result_ready.connect(self._on_result)
        worker.finished.connect(lambda worker=worker: self._on_finished(worker))
        self._worker = worker
        self._workers.append(worker)
        worker.start()

    def _on_finished(self, worker: FeedWorker):
        if self._worker is worker:
            self._worker = None
        if worker in self._workers:
            self._workers.remove(worker)
        worker.deleteLater()

    def _on_result(self, result: FeedResult):
        if result.token != self._token:
            return
        self._has_more = result.has_more
        if result.error and not result.items:
            self._state.setText(tr(f"Could not load: {result.error}", f"加载失败：{result.error}", f"読み込めませんでした: {result.error}"))
        elif not result.items:
            self._state.setText(tr("Nothing here.", "没有更多内容了。", "これ以上ありません。"))
        else:
            self._state.setText("")
        self._grid.set_videos(result.items)
        self._scroll.verticalScrollBar().setValue(0)
        self._update_pager()
        self._sync_selection()

    def _update_pager(self):
        self._page_label.setText(tr(f"Page {self._page + 1}", f"第 {self._page + 1} 页", f"{self._page + 1} ページ"))
        for button in (self._prev_btn, self._prev_bottom):
            button.setEnabled(self._page > 0)
        for button in (self._next_btn, self._next_bottom):
            button.setEnabled(self._has_more)

    def _sync_selection(self):
        selected = self._grid.selected_videos()
        count = len(queueable_ids(selected))
        self._selected_label.setText(tr(f"{len(selected)} selected", f"已选 {len(selected)} 个", f"{len(selected)} 件選択"))
        self._queue_btn.setEnabled(count > 0)
        has_videos = any(v.source_kind != IWARA_IMAGE_SOURCE_KIND for v in self._grid.videos())
        for widget in (self._queue_btn, self._rule_picker):
            widget.setVisible(has_videos or not self._grid.videos())

    def _queue_selected(self):
        videos = self._grid.selected_videos()
        rule_id = self._rule_picker.selected_rule_id()
        self._rule_picker.apply_selected(show_notice=False)
        self.queue_requested.emit(videos, rule_id)
        self._grid.clear_selection()

    def shutdown(self, timeout_ms: int) -> bool:
        return stop_workers(list(self._workers), timeout_ms)


class HomeInterface(QWidget):
    """The Home page: feed → browse → detail, with a back trail."""

    open_settings_requested = Signal()
    return_requested = Signal()  # back out of a detail page opened from another page

    _FEED, _BROWSE, _DETAIL = 0, 1, 2

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("HomeInterface")
        self._fetcher = CoverFetcher(self)
        self._sections = {section.id: section for section in home_sections()}
        self._trail: list[tuple] = [("feed",)]
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

    # ── lifecycle ────────────────────────────────────────────────────────────

    def showEvent(self, event):
        super().showEvent(event)
        if not self._loaded_once:
            self._loaded_once = True
            QTimer.singleShot(0, lambda: self._feed.reload_all(force=True))
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
                self._feed.reload_all(force=True)
            else:
                self._feed.invalidate()

    def _on_login_changed(self, _logged_in: bool):
        if not self._loaded_once:
            return
        if self.isVisible():
            self._feed.reload_subscriptions()
        else:
            self._feed.invalidate(subscriptions_only=True)

    # ── navigation ───────────────────────────────────────────────────────────

    def _show(self, entry: tuple):
        if entry[0] == "origin":
            # The first detail page was opened from another page: go back there.
            self._trail = [("feed",)]
            self._stack.setCurrentIndex(self._FEED)
            self.return_requested.emit()
        elif entry[0] == "feed":
            self._stack.setCurrentIndex(self._FEED)
        elif entry[0] == "browse":
            self._stack.setCurrentIndex(self._BROWSE)
        else:
            _, kind, item_id = entry
            self._detail.load(kind, item_id, None)
            self._stack.setCurrentIndex(self._DETAIL)

    def refresh(self):
        """Reload the feeds, or the open "More" list."""

        if self._stack.currentIndex() == self._BROWSE:
            self._browse.reload()
        elif self._stack.currentIndex() == self._FEED:
            self._feed.reload_all(force=True)

    def go_back(self):
        if len(self._trail) > 1:
            self._back()

    def _back(self):
        if len(self._trail) > 1:
            self._trail.pop()
        self._show(self._trail[-1])

    def _open_browse(self, section_id: str, tab_id: str):
        section = self._sections.get(section_id)
        if section is None:
            return
        tab = section.tab(tab_id)
        if not tab.needs_login:
            # Public rankings are the search page's job: it already pages,
            # sorts, switches view and queues downloads.
            sort = dict(tab.params).get("sort", "")
            signal_bus.search_requested.emit(
                {"scope": "images" if tab.kind == "image" else "videos", "sort": sort}
            )
            return
        self._trail = [("feed",), ("browse",)]
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

        ``external`` means another page asked for it; Back then returns there
        instead of to the Home feed.
        """

        kind = "image" if kind == "image" else "video"
        if external:
            self._trail = [("origin",)]
        elif self._trail[0] == ("origin",) and self._stack.currentIndex() != self._DETAIL:
            self._trail = [("feed",)]
        entry = ("detail", kind, str(item_id))
        if self._trail[-1] != entry:
            self._trail.append(entry)
            # Keep the trail short: browsing related posts must not grow it forever.
            if len(self._trail) > 25:
                self._trail = self._trail[:1] + self._trail[-24:]
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
        accepted = download_manager.enqueue_video_ids(
            ids, source_label=tr("Home", "首页", "ホーム"), rule_id=rule_id or active_rule_id(),
        )
        InfoBar.success(
            title=tr("Added to queue", "已加入队列", "キューに追加"),
            content=tr(f"Accepted {accepted} video(s)", f"已接受 {accepted} 个视频", f"{accepted} 件を追加しました"),
            orient=Qt.Orientation.Horizontal, isClosable=True, position=InfoBarPosition.TOP, duration=3000, parent=self.window(),
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
