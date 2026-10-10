"""The Home feed: every configured section stacked in one scroll area."""

from __future__ import annotations


from PySide6.QtCore import QPoint, QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import BodyLabel, FluentIcon, PushButton, SegmentedWidget, ToolButton

from ...core.home_cache import HomeFeedCache, shared_cache
from ...core.home_feed import home_sections
from ...core.rating import RATING_ALL, normalize_rating, rating_options
from ...i18n import tr
from ..chrome import EmptyState, PageHeader
from .workers import CoverFetcher
from ..media.card_size import CardSizeControl
from ..media.grid import transparent_scroll_area
from ..shortcuts import attach_hint
from ..theme import PAGE_MARGINS, PAGE_SPACING, set_secondary_text
from .section_block import SectionBlock


AGE_TICK_MS = 60_000  # how often visible rows re-check their age


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
