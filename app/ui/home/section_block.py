"""One Home section: a titled, tabbed row of cards with its own cache and fetch worker.

The decisions (cache first, when to re-check, what to do with an answer) live
in ``presentation.home.section_feed``; this widget draws them.
"""

from __future__ import annotations

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

from ...core.home.cache import HomeFeedCache, shared_cache
from ...core.home.feed import HOME_PAGE_LIMIT, FeedSection
from ...presentation.home import section_feed as feed_rules
from ...presentation.home.section_feed import (  # noqa: F401  (re-exported for callers and tests)
    RETRY_AFTER_SECONDS,
    SectionFeed,
    cache_ttl_seconds,
    queueable_ids,
)
from ..common.chrome import SectionTitle
from .workers import CoverFetcher, FeedResult, FeedWorker, stop_workers
from ..media.grid import MediaGrid
from ..style.theme import set_secondary_text
from .page_dependencies import download_manager


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
        self._feed = SectionFeed(section, self._cache)
        self._worker: FeedWorker | None = None
        self._workers: list[FeedWorker] = []  # includes superseded, still-running loads
        self._busy = False

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
        self._tabs.setCurrentItem(self._feed.tab_id)
        self._tabs.setVisible(len(section.tabs) > 1)
        header.addWidget(self._tabs)
        self._age = CaptionLabel("", self)
        set_secondary_text(self._age)
        header.addWidget(self._age)
        self._update_btn = HyperlinkButton("", feed_rules.new_posts_text(), self)
        self._update_btn.clicked.connect(self._apply_pending)
        self._update_btn.hide()
        header.addWidget(self._update_btn)
        header.addStretch(1)
        self._queue_btn = PushButton(feed_rules.queue_text(0, 0), self, FluentIcon.DOWNLOAD)
        self._queue_btn.setToolTip(feed_rules.queue_tooltip())
        self._queue_btn.clicked.connect(self._queue_shown)
        header.addWidget(self._queue_btn)
        self._refresh_btn = ToolButton(FluentIcon.SYNC, self)
        self._refresh_btn.setToolTip(feed_rules.refresh_tooltip(False))
        self._refresh_btn.clicked.connect(lambda: self.reload(force=True))
        header.addWidget(self._refresh_btn)
        self._more_btn = HyperlinkButton("", "", self)
        self._more_btn.clicked.connect(lambda: self.more_requested.emit(self.section.id, self._feed.tab_id))
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
        self._login_btn = PrimaryPushButton(feed_rules.login_button_text(), self._body, FluentIcon.SETTING)
        self._login_btn.clicked.connect(self.settings_requested)
        body.addWidget(self._login_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self._login_btn.hide()
        self._retry_btn = PushButton(feed_rules.retry_text(), self._body, FluentIcon.SYNC)
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
        return self._feed.tab

    @property
    def _token(self) -> int:
        return self._feed.token

    @property
    def _rating(self) -> str:
        return self._feed.rating

    @property
    def _collapsed(self) -> bool:
        return self._feed.collapsed

    def _select_tab(self, tab_id: str):
        if self._feed.select_tab(tab_id):
            self._sync_more()
            self.reload()

    def _sync_more(self):
        self._more_btn.setText(self._feed.more_text())

    def _on_fold_toggled(self, collapsed: bool):
        self._feed.set_collapsed(collapsed)
        self._sync_fold()
        if not self._feed.collapsed:
            self.reload()

    def set_folded(self, folded: bool):
        """Fold or unfold the row (the keyboard / "fold all" route)."""

        if folded != self._feed.collapsed:
            self._title_bar.set_collapsed(folded)
            self._on_fold_toggled(folded)

    def _sync_fold(self):
        collapsed = self._feed.collapsed
        self._body.setVisible(not collapsed)
        self._title_bar.set_collapsed(collapsed)
        self._title_bar.setToolTip(feed_rules.fold_tooltip(collapsed))

    def set_rating(self, rating: str):
        self._feed.set_rating(rating)

    def invalidate(self):
        """Forget what is on screen so the next show re-reads cache/network."""

        self._feed.invalidate()

    def cache_key(self) -> str:
        return self._feed.cache_key()

    def is_stale(self) -> bool:
        """Whether the data on screen is older than the freshness setting."""

        return self._feed.is_stale()

    def refresh_age(self):
        self._age.setText(self._feed.age_text())

    def _sync_buttons(self):
        selected = self._grid.selected_videos()
        shown = self._grid.shown_videos()
        count = len(queueable_ids(selected or shown))
        self._queue_btn.setEnabled(count > 0)
        self._queue_btn.setText(feed_rules.queue_text(len(selected), count))
        self._queue_btn.setVisible(self.tab.kind == "video")
        if not selected and self._feed.pending is not None:
            self._apply_pending()

    def _queue_shown(self):
        videos = self._grid.selected_videos() or self._grid.shown_videos()
        self.queue_requested.emit(videos, "")

    def _set_busy(self, busy: bool):
        self._busy = busy
        self._refresh_btn.setEnabled(not busy)
        self._refresh_btn.setToolTip(feed_rules.refresh_tooltip(busy))

    # ── loading ──────────────────────────────────────────────────────────────

    def reload(self, *, force: bool = False):
        """Show the row, hitting the network only when it is stale (or ``force``)."""

        self._sync_buttons()
        self._retry_btn.hide()
        plan = self._feed.plan_reload(
            force=force, logged_in=download_manager.is_logged_in(), has_items=bool(self._grid.videos())
        )
        if plan.login_required:
            if self._login_btn.isVisibleTo(self):
                return  # already asking the user to sign in
            self._grid.set_videos([])
            self._feed.clear_shown()
            self.refresh_age()
            self._state.setText(feed_rules.login_prompt())
            self._state.show()
            self._login_btn.show()
            return
        self._login_btn.hide()
        if plan.clear:
            self._grid.set_videos([])
            self._feed.clear_shown()
        if plan.show is not None:
            self._show_rows(*plan.show)
        else:
            self.refresh_age()
        if plan.fetch:
            self._fetch(have_items=plan.keep_cards)

    def _show_rows(self, rows: list[dict], saved_at: float, key: str):
        items = feed_rules.items_for(self._feed, rows)
        self._feed.mark_shown(rows, saved_at, key)
        self._update_btn.hide()
        self._grid.set_videos(items)
        if items:
            self._state.hide()
        else:
            self._state.setText(feed_rules.empty_text())
            self._state.show()
        self.refresh_age()
        self._sync_buttons()

    def _fetch(self, *, have_items: bool):
        token, tab, params = self._feed.begin_fetch()
        if self._worker is not None:
            self._worker.requestInterruption()
        if have_items:
            self._set_busy(True)  # keep the cards; just disable the refresh button
        else:
            self._grid.set_loading(True)
            self._state.setText(feed_rules.loading_text())
            self._state.show()
        worker = FeedWorker(
            token, tab.kind, params, 0, HOME_PAGE_LIMIT,
            mode=tab.mode, value=tab.value, rating=self._feed.rating,
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
        outcome = self._feed.on_result(
            result, has_items=bool(self._grid.videos()), has_selection=bool(self._grid.selected_videos())
        )
        if outcome.action == feed_rules.IGNORE:
            return
        self._set_busy(False)
        self._grid.set_loading(False)
        if outcome.action == feed_rules.STASHED:
            return
        if outcome.action == feed_rules.ERROR_KEPT:
            self._age.setText(feed_rules.refresh_failed_text())
            self._age.setToolTip(outcome.error)
            return
        if outcome.action == feed_rules.ERROR:
            self._state.setText(feed_rules.load_failed_text(outcome.error))
            self._state.show()
            self._retry_btn.show()
            return
        self._age.setToolTip("")
        if outcome.action == feed_rules.UNCHANGED:
            self.refresh_age()
        elif outcome.action == feed_rules.PENDING:
            self._update_btn.show()
        else:
            import time

            self._show_rows(outcome.rows or [], time.time(), self.cache_key())

    def _apply_pending(self):
        import time

        rows = self._feed.take_pending()
        self._update_btn.hide()
        if rows is not None:
            self._show_rows(rows, time.time(), self.cache_key())

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
