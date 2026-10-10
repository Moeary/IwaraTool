"""One Home section: a titled, tabbed row of cards with its own cache and fetch worker."""

from __future__ import annotations

import time

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    FluentIcon,
    HyperlinkButton,
    PrimaryPushButton,
    PushButton,
    SegmentedWidget,
    ToolButton,
)

from ...config import app_config
from ...core.home_cache import (
    DEFAULT_CACHE_MINUTES,
    HOME_CACHE_MINUTES_KEY,
    HomeFeedCache,
    cache_key,
    rows_signature,
    shared_cache,
)
from ...core.home_feed import HOME_PAGE_LIMIT, FeedSection
from ...core.rating import RATING_ALL, normalize_rating
from ...core.search import SearchVideo
from ...i18n import tr
from ..chrome import SectionTitle, format_age
from .workers import CoverFetcher, FeedResult, FeedWorker, items_from_rows, stop_workers
from ..media_card import MediaGrid
from ..theme import set_secondary_text
from .page_dependencies import download_manager


RETRY_AFTER_SECONDS = 120  # wait this long before re-trying a failed background check


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
        root.addLayout(self._build_header(section))

        root.addWidget(self._build_body(fetcher))
        self._sync_more()
        self._sync_fold()
        self._sync_buttons()

    def _build_header(self, section: FeedSection) -> QHBoxLayout:
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
        return header

    def _build_body(self, fetcher: CoverFetcher) -> QWidget:
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
        return self._body

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
