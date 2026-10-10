"""Full-page, paged view of one Home section (the "More" button)."""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    FluentIcon,
    PrimaryPushButton,
    PushButton,
    SegmentedWidget,
    SubtitleLabel,
    ToolButton,
)

from ...core.home_feed import BROWSE_PAGE_LIMIT, FeedSection
from ...core.rating import RATING_ALL, normalize_rating
from ...core.search import IWARA_IMAGE_SOURCE_KIND, SearchVideo
from ...i18n import tr
from .workers import CoverFetcher, FeedResult, FeedWorker, stop_workers
from ..media.card_size import CardSizeControl
from ..media.grid import MediaGrid, transparent_scroll_area
from ..navigation import has_focus_within, restore_scroll
from ..rules_page import RulePicker
from ..theme import PAGE_MARGINS, PAGE_SPACING, set_secondary_text
from .section_block import queueable_ids


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

    def __init__(self, fetcher: CoverFetcher, parent: QWidget | None = None, *, configurable_clicks: bool = False):
        super().__init__(parent)
        self._configurable_clicks = configurable_clicks
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

        root.addLayout(self._build_bar())
        root.addLayout(self._build_tools())

        self._state = CaptionLabel("", self)
        set_secondary_text(self._state)
        root.addWidget(self._state)

        root.addWidget(self._build_scroll(fetcher), 1)
        self._sync_selection()

    def _build_bar(self) -> QHBoxLayout:
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
        return bar

    def _build_tools(self) -> QHBoxLayout:
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
        return tools

    def _build_scroll(self, fetcher: CoverFetcher):
        self._scroll = transparent_scroll_area("HomeBrowseScroll", self)
        page = QWidget()
        column = QVBoxLayout(page)
        column.setContentsMargins(0, 0, 8, 24)
        column.setSpacing(12)
        self._grid = MediaGrid(
            page, selectable=True, resizable=True, configurable_clicks=self._configurable_clicks,
        )
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
        return self._scroll

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
