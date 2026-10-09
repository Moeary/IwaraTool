"""Home: account feed, hot videos and hot images, with in-app detail pages.

The page is a small stack — the feed, a paginated "More" view and a post
detail view.  Each move records where the user was in the window's shared back
history (``navigation``), so Back returns to exactly that list or post, while
the batch download tools stay one click away (select cards, pick a rule, queue).
"""
from __future__ import annotations

import time
import webbrowser
from dataclasses import dataclass, field, replace as dataclass_replace
from typing import Any

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
    ToolButton,
)

from ..config import app_config
from ..core.home_cache import (
    DEFAULT_CACHE_MINUTES,
    HOME_CACHE_MINUTES_KEY,
    HomeFeedCache,
    cache_key,
    rows_signature,
    shared_cache,
)
from ..core.home_feed import (
    BROWSE_PAGE_LIMIT,
    HOME_PAGE_LIMIT,
    MODE_AUTHOR,
    FeedSection,
    home_sections,
)
from ..core.manager import download_manager
from ..core.rating import RATING_ALL, UI_RATING_KEY, normalize_rating, rating_options
from ..core.rules import active_rule_id
from ..core.search import IWARA_IMAGE_SOURCE_KIND, SearchVideo, avatar_url
from ..i18n import tr
from ..signal_bus import signal_bus
from .chrome import EmptyState, PageHeader, SectionTitle, format_age
from .home_workers import CoverFetcher, FeedResult, FeedWorker, items_from_rows, stop_workers
from .media_card import CardSizeControl, MediaGrid, transparent_scroll_area
from .media_detail import DetailState, DetailView
from .navigation import has_focus_within, navigate_back, record_navigation, restore_scroll
from .rules_page import RulePicker
from .shortcuts import attach_hint
from .theme import PAGE_MARGINS, PAGE_SPACING, set_secondary_text

RETRY_AFTER_SECONDS = 120  # wait this long before re-trying a failed background check
AGE_TICK_MS = 60_000  # how often visible rows re-check their age


def queueable_ids(videos: list[SearchVideo]) -> list[str]:
    """Iwara video ids that can be downloaded (image posts and embeds cannot)."""

    seen: set[str] = set()
    ids: list[str] = []
    for video in videos:
        if video.source_kind == "iwara" and video.downloadable and video.video_id not in seen:
            seen.add(video.video_id)
            ids.append(video.video_id)
    return ids


def cache_ttl_seconds() -> float:
    """How long a cached row counts as fresh; 0 means "only refresh when asked"."""

    try:
        return max(0, int(app_config.get_ui_value(HOME_CACHE_MINUTES_KEY, DEFAULT_CACHE_MINUTES))) * 60.0
    except (TypeError, ValueError):
        return DEFAULT_CACHE_MINUTES * 60.0


class SectionBlock(QWidget):
    """One titled row of cards on the feed.

    The row paints from the on-disk cache first.  A background re-check runs
    only when the cached copy is older than the "refresh after" setting (or the
    user presses refresh) and the grid is rebuilt only if the posts changed.
    """

    more_requested = Signal(str, str)  # section id, tab id
    queue_requested = Signal(list, str)
    open_requested = Signal(object)
    context_requested = Signal(object, QPoint)
    settings_requested = Signal()

    def __init__(
        self,
        section: FeedSection,
        fetcher: CoverFetcher,
        parent: QWidget | None = None,
        *,
        cache: HomeFeedCache | None = None,
    ):
        super().__init__(parent)
        self.section = section
        self._cache = cache if cache is not None else shared_cache()
        self._rating = RATING_ALL
        self._token = 0
        self._worker: FeedWorker | None = None
        self._workers: list[FeedWorker] = []  # includes superseded, still-running loads
        self._shown_key = ""
        self._shown_signature: tuple[str, ...] | None = None
        self._saved_at = 0.0  # epoch seconds of the data on screen
        self._last_attempt = 0.0  # monotonic, throttles failed background checks
        self._pending: FeedResult | None = None  # newer rows held back while cards are selected
        self._busy = False
        saved = str(app_config.get_ui_value(f"home_tab_{section.id}_v1", "") or "")
        self._tab_id = section.tab(saved).id
        self._collapsed = str(app_config.get_ui_value(f"home_collapsed_{section.id}_v1", "0")) in {"1", "true", "True"}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        header = QHBoxLayout()
        header.setSpacing(10)
        self._title_bar = SectionTitle(section.title, self, collapsible=True)
        self._title_bar.toggled.connect(self._on_fold_toggled)
        header.addWidget(self._title_bar)
        self._tabs = SegmentedWidget(self)
        for tab in section.tabs:
            self._tabs.addItem(tab.id, tab.label, lambda _=False, tab_id=tab.id: self._select_tab(tab_id))
        self._tabs.setCurrentItem(self._tab_id)
        self._tabs.setVisible(len(section.tabs) > 1)
        header.addWidget(self._tabs)
        self._age = CaptionLabel("", self)
        set_secondary_text(self._age)
        header.addWidget(self._age)
        self._update_btn = HyperlinkButton("", tr("New posts — show", "有新内容，点击显示", "新着あり — 表示"), self)
        self._update_btn.clicked.connect(self._apply_pending)
        self._update_btn.hide()
        header.addWidget(self._update_btn)
        header.addStretch(1)
        self._queue_btn = PushButton(tr("Download shown", "下载当前展示", "表示中をダウンロード"), self, FluentIcon.DOWNLOAD)
        self._queue_btn.setToolTip(tr("Queue every video shown in this row", "把这一行展示的视频全部加入下载队列", "この行の動画をすべてキューに追加"))
        self._queue_btn.clicked.connect(self._queue_shown)
        header.addWidget(self._queue_btn)
        self._refresh_btn = ToolButton(FluentIcon.SYNC, self)
        self._refresh_btn.setToolTip(tr("Refresh this row now", "立即刷新这一行", "この行を今すぐ更新"))
        self._refresh_btn.clicked.connect(lambda: self.reload(force=True))
        header.addWidget(self._refresh_btn)
        self._more_btn = HyperlinkButton("", "", self)
        self._more_btn.clicked.connect(lambda: self.more_requested.emit(self.section.id, self._tab_id))
        header.addWidget(self._more_btn)
        root.addLayout(header)

        self._body = QWidget(self)
        body = QVBoxLayout(self._body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(8)
        self._state = BodyLabel("", self._body)
        self._state.setWordWrap(True)
        set_secondary_text(self._state)
        body.addWidget(self._state)
        self._login_btn = PrimaryPushButton(tr("Open Settings to sign in", "前往设置登录", "設定でログイン"), self._body, FluentIcon.SETTING)
        self._login_btn.clicked.connect(self.settings_requested)
        body.addWidget(self._login_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self._login_btn.hide()
        self._retry_btn = PushButton(tr("Try again", "重试", "再試行"), self._body, FluentIcon.SYNC)
        self._retry_btn.clicked.connect(lambda: self.reload(force=True))
        body.addWidget(self._retry_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self._retry_btn.hide()

        self._grid = MediaGrid(self._body, selectable=True, max_rows=2, resizable=True)
        self._grid.bind_fetcher(fetcher)
        self._grid.card_activated.connect(self.open_requested)
        self._grid.card_context_requested.connect(self.context_requested)
        self._grid.selection_changed.connect(self._sync_buttons)
        body.addWidget(self._grid)
        root.addWidget(self._body)
        self._sync_more()
        self._sync_fold()
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
        self._shown_key = ""
        self._sync_more()
        self.reload()

    def _sync_more(self):
        tab = self.tab
        self._more_btn.setText(
            tr("More in Search ›", "在搜索页查看更多 ›", "検索ページで見る ›")
            if tab.opens_in_search
            else tr("More ›", "查看更多 ›", "もっと見る ›")
        )

    def _on_fold_toggled(self, collapsed: bool):
        self._collapsed = collapsed
        app_config.set_ui_value(f"home_collapsed_{self.section.id}_v1", "1" if self._collapsed else "0")
        self._sync_fold()
        if not self._collapsed:
            self.reload()

    def set_folded(self, folded: bool):
        """Fold or unfold the row (the keyboard / "fold all" route)."""

        if folded != self._collapsed:
            self._title_bar.set_collapsed(folded)
            self._on_fold_toggled(folded)

    def _sync_fold(self):
        self._body.setVisible(not self._collapsed)
        self._title_bar.set_collapsed(self._collapsed)
        self._title_bar.setToolTip(
            tr("Click to expand this row", "点击展开这一行", "クリックで展開") if self._collapsed else tr("Click to fold this row", "点击折叠这一行", "クリックで折りたたむ")
        )

    def set_rating(self, rating: str):
        self._rating = normalize_rating(rating)

    def invalidate(self):
        """Forget what is on screen so the next show re-reads cache/network."""

        self._last_attempt = 0.0

    def cache_key(self) -> str:
        return cache_key(self.section.id, self.tab.id, self._rating, account=self.tab.needs_login)

    def is_stale(self) -> bool:
        """Whether the data on screen is older than the freshness setting."""

        ttl = cache_ttl_seconds()
        if ttl <= 0:
            return not self._saved_at
        return not self._saved_at or time.time() - self._saved_at > ttl

    def refresh_age(self):
        if not self._saved_at:
            self._age.setText("")
            return
        text = format_age(time.time() - self._saved_at)
        self._age.setText(tr(f"Updated {text}", f"更新于{text}", f"{text}に更新"))

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
        if not selected and self._pending is not None:
            self._apply_pending()

    def _queue_shown(self):
        videos = self._grid.selected_videos() or self._grid.shown_videos()
        self.queue_requested.emit(videos, "")

    def _set_busy(self, busy: bool):
        self._busy = busy
        self._refresh_btn.setEnabled(not busy)
        self._refresh_btn.setToolTip(
            tr("Refreshing…", "正在刷新…", "更新中…") if busy else tr("Refresh this row now", "立即刷新这一行", "この行を今すぐ更新")
        )

    # ── loading ──────────────────────────────────────────────────────────────

    def reload(self, *, force: bool = False):
        """Show the row, hitting the network only when it is stale (or ``force``)."""

        tab = self.tab
        self._sync_buttons()
        self._retry_btn.hide()
        if tab.needs_login and not download_manager.is_logged_in():
            if self._login_btn.isVisibleTo(self):
                return  # already asking the user to sign in
            self._grid.set_videos([])
            self._shown_key, self._shown_signature, self._saved_at = "", None, 0.0
            self.refresh_age()
            self._state.setText(tr(
                "Sign in to see the newest uploads from the creators you follow on Iwara.",
                "登录 Iwara 账号后，这里会显示你关注作者的最新作品。",
                "Iwaraにログインすると、フォロー中の作者の最新作がここに表示されます。",
            ))
            self._state.show()
            self._login_btn.show()
            return
        self._login_btn.hide()
        if self._collapsed:
            return  # nothing to show or fetch until it is expanded
        key = self.cache_key()
        entry = self._cache.get(key)
        if entry is not None and (key != self._shown_key or not self._grid.videos()):
            self._show_rows(entry.rows, entry.saved_at, key)
        elif entry is not None:
            self._saved_at = entry.saved_at
            self.refresh_age()
        if entry is not None and not force:
            ttl = cache_ttl_seconds()
            if ttl <= 0 or entry.is_fresh(ttl):
                return
            if self._last_attempt and time.monotonic() - self._last_attempt < RETRY_AFTER_SECONDS:
                return
        self._fetch(have_items=entry is not None or bool(self._grid.videos()))

    def _show_rows(self, rows: list[dict], saved_at: float, key: str):
        items = items_from_rows(self.tab.kind, rows, self.tab.mode, self._rating)
        self._pending = None
        self._update_btn.hide()
        self._shown_key = key
        self._shown_signature = rows_signature(rows)
        self._saved_at = saved_at
        self._grid.set_videos(items)
        if items:
            self._state.hide()
        else:
            self._state.setText(tr("Nothing here yet.", "这里暂时没有内容。", "まだ何もありません。"))
            self._state.show()
        self.refresh_age()
        self._sync_buttons()

    def _fetch(self, *, have_items: bool):
        tab = self.tab
        self._token += 1
        self._last_attempt = time.monotonic()
        if self._worker is not None:
            self._worker.requestInterruption()
        if have_items:
            self._set_busy(True)  # keep the cards; just disable the refresh button
        else:
            self._grid.set_loading(True)
            self._state.setText(tr("Loading…", "加载中…", "読み込み中…"))
            self._state.show()
        worker = FeedWorker(
            self._token, tab.kind, tab.request_params(self._rating), 0, HOME_PAGE_LIMIT,
            mode=tab.mode, value=tab.value, rating=self._rating,
        )
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
        self._set_busy(False)
        self._grid.set_loading(False)
        key = self.cache_key()
        if result.error and not result.items:
            if self._grid.videos():
                # Keep what is on screen; just say the check failed.
                self._age.setText(tr("Couldn't refresh", "刷新失败", "更新に失敗"))
                self._age.setToolTip(result.error)
            else:
                self._state.setText(tr(f"Could not load: {result.error}", f"加载失败：{result.error}", f"読み込めませんでした: {result.error}"))
                self._state.show()
                self._retry_btn.show()
            return
        self._age.setToolTip("")
        changed = self._cache.put(key, result.rows)
        if key == self._shown_key and not changed and self._grid.videos():
            self._saved_at = time.time()  # same posts: leave the grid alone
            self.refresh_age()
            return
        if changed and self._grid.videos() and self._grid.selected_videos():
            # Do not pull cards out from under a selection in progress.
            self._pending = result
            self._update_btn.show()
            return
        self._show_rows(result.rows, time.time(), key)

    def _apply_pending(self):
        pending, self._pending = self._pending, None
        self._update_btn.hide()
        if pending is not None:
            self._show_rows(pending.rows, time.time(), self.cache_key())

    def shutdown(self, timeout_ms: int) -> bool:
        return stop_workers(list(self._workers), timeout_ms)

    def retire(self):
        """Stop showing this row; its in-flight loads finish in the background."""

        for worker in list(self._workers):
            try:
                worker.requestInterruption()
            except RuntimeError:
                pass
        self.hide()
        self.deleteLater()  # no setParent(None): re-parenting a styled widget tree is slow


class HomeFeedView(QWidget):
    """Header (rating selector, refresh) over the scrolling sections."""

    more_requested = Signal(str, str)
    queue_requested = Signal(list, str)
    open_requested = Signal(object)
    context_requested = Signal(object, QPoint)
    settings_requested = Signal()
    rating_selected = Signal(str)
    customize_requested = Signal()

    def __init__(self, fetcher: CoverFetcher, parent: QWidget | None = None, *, cache: HomeFeedCache | None = None):
        super().__init__(parent)
        self._fetcher = fetcher
        self._cache = cache if cache is not None else shared_cache()
        self._rating_value = RATING_ALL
        self._retired: list[SectionBlock] = []
        root = QVBoxLayout(self)
        root.setContentsMargins(*PAGE_MARGINS)
        root.setSpacing(PAGE_SPACING)

        self._header = PageHeader(tr("Home", "首页", "ホーム"), self)
        self._header.tools.addWidget(CardSizeControl(self))
        self._header.tools.addWidget(self._rating_selector())
        self._customize_btn = ToolButton(FluentIcon.EDIT, self)
        attach_hint(self._customize_btn, tr("Customize Home rows", "自定义首页栏目", "ホームの欄をカスタマイズ"), "home_customize")
        self._customize_btn.clicked.connect(self.customize_requested)
        self._header.tools.addWidget(self._customize_btn)
        self._refresh_all_btn = PushButton(tr("Refresh all", "全部刷新", "すべて更新"), self, FluentIcon.SYNC)
        attach_hint(self._refresh_all_btn, tr("Re-check every row now", "立即重新检查所有栏目", "すべての欄を今すぐ再確認"), "home_refresh")
        self._refresh_all_btn.clicked.connect(lambda: self.reload_all(force=True))
        self._header.tools.addWidget(self._refresh_all_btn)
        root.addWidget(self._header)

        self._scroll = transparent_scroll_area("HomeFeedScroll", self)
        page = QWidget()
        self._column = QVBoxLayout(page)
        self._column.setContentsMargins(0, 0, 8, 24)
        self._column.setSpacing(24)
        self._column.addStretch(1)
        self._scroll.setWidget(page)
        self._page = page
        self._empty = EmptyState(
            FluentIcon.HOME,
            tr("Nothing on your Home page", "首页还没有栏目", "ホームに欄がありません"),
            tr("Add rows such as a tag search, an author or the newest uploads.", "添加标签搜索、作者作品或最新上传等栏目。", "タグ検索・作者・新着などの欄を追加できます。"),
            page,
            action_text=tr("Customize Home", "自定义首页", "ホームをカスタマイズ"),
        )
        self._empty.action_clicked.connect(self.customize_requested)
        self._column.insertWidget(0, self._empty)
        root.addWidget(self._scroll, 1)

        self._age_timer = QTimer(self)
        self._age_timer.setInterval(AGE_TICK_MS)
        self._age_timer.timeout.connect(self._on_tick)
        self.blocks: list[SectionBlock] = []
        self.rebuild()

    def _rating_selector(self) -> QWidget:
        holder = QWidget(self)
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        label = BodyLabel(tr("Content", "内容分级", "コンテンツ"), holder)
        set_secondary_text(label)
        row.addWidget(label)
        self._rating = SegmentedWidget(holder)
        for text, value in rating_options():
            self._rating.addItem(value, text, lambda _=False, value=value: self.rating_selected.emit(value))
        row.addWidget(self._rating)
        return holder

    def rebuild(self, specs=None):
        """Bring the rows in line with the saved layout.

        Rows whose definition did not change are kept as they are (cards,
        covers, scroll position and all); only added rows are built and only
        removed rows are dropped, so adding one row costs one row.
        """

        sections = home_sections(specs)
        existing = {block.section: block for block in self.blocks}
        blocks: list[SectionBlock] = []
        for section in sections:
            block = existing.pop(section, None)
            if block is None:
                block = SectionBlock(section, self._fetcher, self._page, cache=self._cache)
                block.set_rating(self._rating_value)
                block.more_requested.connect(self.more_requested)
                block.queue_requested.connect(self.queue_requested)
                block.open_requested.connect(self.open_requested)
                block.context_requested.connect(self.context_requested)
                block.settings_requested.connect(self.settings_requested)
            blocks.append(block)
        for block in existing.values():
            self._column.removeWidget(block)
            block.retire()
            self._retired.append(block)
        self._retired = [b for b in self._retired if b._workers]
        self.blocks = blocks
        # Rows sit after the empty-state widget (index 0) in layout order.
        for index, block in enumerate(blocks, start=1):
            if self._column.indexOf(block) != index:
                self._column.removeWidget(block)
                self._column.insertWidget(index, block)
            block.show()
        self._empty.setVisible(not sections)

    def set_rating(self, rating: str):
        rating = normalize_rating(rating)
        self._rating_value = rating
        self._rating.setCurrentItem(rating)
        for block in self.blocks:
            block.set_rating(rating)

    def reload_all(self, *, force: bool = False):
        for block in self.blocks:
            block.reload(force=force)

    def reload_subscriptions(self):
        """The account changed: its cached rows belong to someone else."""

        self._cache.clear(account_only=True)
        for block in self.blocks:
            if block.tab.needs_login:
                block.invalidate()
                block.reload(force=True)

    def invalidate(self, *, subscriptions_only: bool = False):
        """Mark rows stale so the next time Home is shown they reload."""

        if subscriptions_only:
            self._cache.clear(account_only=True)
        for block in self.blocks:
            if not subscriptions_only or block.tab.needs_login:
                block.invalidate()

    def _on_tick(self):
        for block in self.blocks:
            block.refresh_age()
        if self.isVisible():
            self.reload_all()  # only rows that went stale hit the network

    def showEvent(self, event):
        super().showEvent(event)
        self._age_timer.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._age_timer.stop()

    def shutdown(self, timeout_ms: int) -> bool:
        self._age_timer.stop()
        return all([block.shutdown(timeout_ms) for block in [*self.blocks, *self._retired]])


@dataclass
class BrowseState:
    """Everything a "More" list shows, so Back can show it again without the network."""

    section: FeedSection
    tab_id: str
    rating: str
    page: int
    has_more: bool
    items: list[SearchVideo] = field(default_factory=list)
    status: str = ""
    scroll: int = 0
    selected: tuple[str, ...] = ()
    cursor: int = -1
    focused: bool = False
    loaded: bool = False  # False: the page had not arrived yet, so it is asked for again


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
        self._loaded = False
        self._worker: FeedWorker | None = None
        self._workers: list[FeedWorker] = []

        root = QVBoxLayout(self)
        self._root = root
        root.setContentsMargins(*PAGE_MARGINS)
        root.setSpacing(PAGE_SPACING)

        bar = QHBoxLayout()
        self._bar = bar
        bar.setSpacing(12)
        self._back_btn = PushButton(tr("Back", "返回", "戻る"), self, FluentIcon.LEFT_ARROW)
        self._back_btn.clicked.connect(self.back_requested)
        bar.addWidget(self._back_btn)
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

    def set_embedded(self, embedded: bool):
        """Hide the back button and title when a host page supplies its own."""

        self._back_btn.setVisible(not embedded)
        self._title.setVisible(not embedded)
        if embedded:
            self._root.setContentsMargins(0, 0, 0, 0)  # the host page supplies the margins

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

    @property
    def section(self) -> FeedSection | None:
        return self._section

    def view_state(self) -> BrowseState | None:
        if self._section is None:
            return None
        return BrowseState(
            section=self._section,
            tab_id=self._tab_id,
            rating=self._rating,
            page=self._page,
            has_more=self._has_more,
            items=list(self._grid.videos()),
            status=self._state.text(),
            scroll=self._scroll.verticalScrollBar().value(),
            selected=tuple(v.video_id for v in self._grid.selected_videos()),
            cursor=self._grid.cursor_index(),
            focused=has_focus_within(self._grid),
            loaded=self._loaded,
        )

    def restore_state(self, state: BrowseState):
        """Show a recorded list again: same tab, page, cards, selection and scroll."""

        self._section = state.section
        self._tab_id = state.section.tab(state.tab_id).id
        self._rating = normalize_rating(state.rating)
        self._title.setText(state.section.title)
        self._rebuild_tabs()
        if not state.loaded:
            self._go(state.page)
            return
        self._token += 1  # whatever is still loading belongs to another list
        if self._worker is not None:
            self._worker.requestInterruption()
        self._page = max(0, state.page)
        self._has_more = state.has_more
        self._loaded = True
        self._state.setText(state.status)
        self._grid.set_videos(list(state.items))
        self._grid.select_ids(set(state.selected))
        self._grid.set_cursor(state.cursor)
        if state.focused:
            self._grid.setFocus(Qt.FocusReason.OtherFocusReason)
        restore_scroll(self._scroll, state.scroll)
        self._update_pager()
        self._sync_selection()

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
        self._loaded = False
        self._state.setText(tr("Loading…", "加载中…", "読み込み中…"))
        self._grid.set_videos([])
        self._update_pager()
        worker = FeedWorker(
            self._token, tab.kind, tab.request_params(self._rating), self._page, BROWSE_PAGE_LIMIT,
            mode=tab.mode, value=tab.value, rating=self._rating,
        )
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
        self._loaded = True
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

        from .home_layout_dialog import HomeLayoutDialog

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
