"""One subscription source as a paged poster grid.

Every download, refresh and delete goes through the page, so this view
and the classic table behave the same."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import (
    AvatarWidget,
    BodyLabel,
    CaptionLabel,
    ComboBox,
    FluentIcon,
    PrimaryPushButton,
    PushButton,
    SearchLineEdit,
    SubtitleLabel,
    ToolButton,
)

from ...core.search.models import SearchVideo
from ...i18n import tr
from ..author_status import AuthorStatusBar
from ..media.card_size import CardSizeControl
from ..media.grid import MediaGrid, transparent_scroll_area
from ..media.pixmaps import PixmapLRU
from ..navigation import has_focus_within, restore_scroll
from ..rules.rule_picker import RulePicker
from .helpers import _source_type_label
from ..theme import PAGE_SPACING, set_secondary_text
from ..flow_layout import ResponsiveFlowLayout
from .cover_loader import CoverLoader, load_pixmap
from .item_queries import downloadable_ids, filter_items, item_state, item_to_video, sort_items


GRID_PAGE_SIZE = 60


@dataclass
class SourceViewState:
    """One subscription's grid as the user left it (filters, page, selection, scroll)."""

    source_id: int
    search: str = ""
    state_filter: str = "all"
    order: str = "date_desc"
    page_index: int = 0
    scroll: int = 0
    selected: tuple[str, ...] = ()
    cursor: int = -1
    focused: bool = False


class SourceItemsView(QWidget):
    """A subscription's videos as selectable posters, with the download tools."""

    back_requested = Signal()

    def __init__(self, page, covers: CoverLoader, parent: QWidget | None = None):
        super().__init__(parent)
        self._page = page
        self._covers = covers
        self._source: dict[str, Any] = {}
        self._items: list[dict[str, Any]] = []
        self._filtered: list[dict[str, Any]] = []
        self._pixmaps = PixmapLRU()
        self._page_index = 0
        self._stale = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(PAGE_SPACING)
        root.addLayout(self._build_head(page))

        # Whether this author is subscribed here / followed on Iwara, with the buttons to change it.
        self.status_bar = AuthorStatusBar(self, show_open=False)
        self.status_bar.hide()
        root.addWidget(self.status_bar)
        root.addLayout(self._build_filters())
        root.addLayout(self._build_tools())

        self._status = CaptionLabel("", self)
        set_secondary_text(self._status)
        root.addWidget(self._status)
        self._build_grid(root, page)

        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(180)
        self._filter_timer.timeout.connect(lambda: self._apply(reset_page=True))
        covers.cover_ready.connect(self._on_cover_ready)
        covers.queue_changed.connect(self._sync_status)
        self._sync_buttons()

    def _build_head(self, page) -> QHBoxLayout:
        head = QHBoxLayout()
        head.setSpacing(12)
        back = PushButton(tr("Back", "返回", "戻る"), self, FluentIcon.LEFT_ARROW)
        back.clicked.connect(self.back_requested)
        head.addWidget(back)
        self._avatar = AvatarWidget(self)
        self._avatar.setRadius(22)
        head.addWidget(self._avatar)
        names = QVBoxLayout()
        names.setSpacing(0)
        self._title = SubtitleLabel("", self)
        self._subtitle = CaptionLabel("", self)
        set_secondary_text(self._subtitle)
        names.addWidget(self._title)
        names.addWidget(self._subtitle)
        head.addLayout(names, 1)
        refresh = PushButton(tr("Refresh", "刷新", "更新"), self, FluentIcon.SYNC)
        refresh.setToolTip(tr("Fetch this source's newest videos", "获取此订阅源的最新视频", "最新の動画を取得"))
        refresh.clicked.connect(self._refresh_source)
        head.addWidget(refresh)
        self._page_btn = PushButton(tr("Open page", "打开主页", "ページを開く"), self, FluentIcon.GLOBE)
        self._page_btn.clicked.connect(self._open_source_page)
        head.addWidget(self._page_btn)
        more = ToolButton(FluentIcon.MORE, self)
        more.clicked.connect(lambda: page._show_source_menu(self.source_id, more.mapToGlobal(more.rect().bottomLeft())))
        head.addWidget(more)
        return head

    def _build_filters(self) -> ResponsiveFlowLayout:
        filters = ResponsiveFlowLayout(spacing=8)
        self._search = SearchLineEdit(self)
        self._search.setPlaceholderText(tr("Search titles…", "搜索标题…", "タイトルを検索…"))
        self._search.setClearButtonEnabled(True)
        self._search.setMinimumWidth(220)
        self._search.textChanged.connect(lambda _t: self._filter_timer.start())
        filters.addWidget(self._search)
        self._state_combo = ComboBox(self)
        for key, label in (
            ("all", tr("All", "全部", "すべて")),
            ("ready", tr("Downloadable", "可下载", "保存可能")),
            ("new", tr("New", "新增", "新規")),
            ("downloaded", tr("Downloaded", "已下载", "保存済み")),
            ("moved", tr("Moved", "已移走", "移動済み")),
            ("queued", tr("Queued", "已入队", "キュー内")),
            ("unavailable", tr("Unavailable", "不可下载", "保存不可")),
        ):
            self._state_combo.addItem(label)
            self._state_combo.setItemData(self._state_combo.count() - 1, key)
        self._state_combo.setFixedWidth(124)
        self._state_combo.currentIndexChanged.connect(lambda _i: self._apply(reset_page=True))
        filters.addWidget(self._state_combo)
        self._sort_combo = ComboBox(self)
        for key, label in (
            ("date_desc", tr("Newest first", "时间倒序", "新しい順")),
            ("date_asc", tr("Oldest first", "时间升序", "古い順")),
            ("new_first", tr("New first", "新增优先", "新規優先")),
            ("title", tr("Title A-Z", "标题 A-Z", "タイトル A-Z")),
        ):
            self._sort_combo.addItem(label)
            self._sort_combo.setItemData(self._sort_combo.count() - 1, key)
        self._sort_combo.setFixedWidth(124)
        self._sort_combo.currentIndexChanged.connect(lambda _i: self._apply(reset_page=True))
        filters.addWidget(self._sort_combo)
        filters.addWidget(CardSizeControl(self))
        return filters

    def _build_tools(self) -> ResponsiveFlowLayout:
        tools = ResponsiveFlowLayout(spacing=8)
        select_all = PushButton(tr("Select all", "全选", "すべて選択"), self)
        select_all.clicked.connect(lambda: self._grid.select_all(True))
        clear = PushButton(tr("Clear", "取消选择", "選択解除"), self)
        clear.clicked.connect(lambda: self._grid.clear_selection())
        tools.addWidget(select_all)
        tools.addWidget(clear)
        self._selected_label = CaptionLabel("", self)
        set_secondary_text(self._selected_label)
        tools.addWidget(self._selected_label)
        self._rule_picker = RulePicker(self)
        tools.addWidget(self._rule_picker)
        self._download_selected_btn = PrimaryPushButton(tr("Download selected", "下载选中", "選択を保存"), self, FluentIcon.DOWNLOAD)
        self._download_selected_btn.clicked.connect(self._download_selected)
        tools.addWidget(self._download_selected_btn)
        self._download_new_btn = PushButton(tr("Download new", "下载新增", "新規を保存"), self, FluentIcon.DOWNLOAD)
        self._download_new_btn.clicked.connect(self._download_new)
        tools.addWidget(self._download_new_btn)
        self._download_all_btn = PushButton(tr("Download all shown", "下载全部筛选结果", "表示中をすべて保存"), self, FluentIcon.DOWNLOAD)
        self._download_all_btn.clicked.connect(self._download_all)
        tools.addWidget(self._download_all_btn)
        return tools

    def _build_grid(self, root: QVBoxLayout, page):
        self._scroll = transparent_scroll_area("SubscriptionSourceScroll", self)
        body = QWidget()
        column = QVBoxLayout(body)
        column.setContentsMargins(0, 0, 8, 24)
        column.setSpacing(12)
        self._grid = MediaGrid(body, selectable=True, resizable=True, configurable_clicks=True)
        self._grid.card_activated.connect(page._show_video_detail)
        self._grid.card_context_requested.connect(self._on_card_context)
        self._grid.selection_changed.connect(self._sync_buttons)
        column.addWidget(self._grid)
        footer = QHBoxLayout()
        footer.addStretch(1)
        self._prev_btn = PushButton(tr("Previous page", "上一页", "前のページ"), body, FluentIcon.LEFT_ARROW)
        self._prev_btn.clicked.connect(lambda: self._go(self._page_index - 1))
        self._page_label = BodyLabel("", body)
        self._next_btn = PushButton(tr("Next page", "下一页", "次のページ"), body, FluentIcon.RIGHT_ARROW)
        self._next_btn.clicked.connect(lambda: self._go(self._page_index + 1))
        footer.addWidget(self._prev_btn)
        footer.addWidget(self._page_label)
        footer.addWidget(self._next_btn)
        footer.addStretch(1)
        column.addLayout(footer)
        column.addStretch(1)
        self._scroll.setWidget(body)
        root.addWidget(self._scroll, 1)

    # ── data ─────────────────────────────────────────────────────────────────

    @property
    def source_id(self) -> int:
        return int(self._source.get("id", 0) or 0)

    def open_source(self, source: dict[str, Any]):
        self._source = dict(source)
        title = str(source.get("title", "") or source.get("source_key", "") or "")
        self._title.setText(title)
        key = str(source.get("source_key", "") or "")
        is_author = str(source.get("source_type", "") or "") == "author"
        self._subtitle.setText(
            (f"@{key}" if is_author else key) + "  ·  " + _source_type_label(str(source.get("source_type", "") or ""))
        )
        self._avatar.setText(title[:1].upper())
        pixmap = load_pixmap(str(source.get("avatar_path", "") or ""), 120)
        if pixmap is not None:
            self._avatar.setImage(pixmap)
        self.status_bar.setVisible(is_author)
        if is_author:
            self.status_bar.set_author(
                key, title, str(source.get("remote_id", "") or ""), str(source.get("avatar_url", "") or ""),
            )
        self._search.blockSignals(True)
        self._search.clear()
        self._search.blockSignals(False)
        self._state_combo.setCurrentIndex(0)
        self._page_btn.setEnabled(bool(self._page._source_page_url(source)))
        self._stale = False
        self._items = self._page._source_items(self.source_id) if self.source_id else []
        self._apply(reset_page=True)

    def view_state(self) -> SourceViewState:
        return SourceViewState(
            source_id=self.source_id,
            search=self._search.text(),
            state_filter=str(self._state_combo.currentData() or "all"),
            order=str(self._sort_combo.currentData() or "date_desc"),
            page_index=self._page_index,
            scroll=self._scroll.verticalScrollBar().value(),
            selected=tuple(v.video_id for v in self._grid.selected_videos()),
            cursor=self._grid.cursor_index(),
            focused=has_focus_within(self._grid),
        )

    def restore_state(self, source: dict[str, Any], state: SourceViewState):
        """Re-open ``source`` on the recorded filters, page, selection and scroll (local data only)."""

        self.open_source(source)
        for combo, value in ((self._state_combo, state.state_filter), (self._sort_combo, state.order)):
            index = combo.findData(value)
            if index >= 0:
                combo.blockSignals(True)
                combo.setCurrentIndex(index)
                combo.blockSignals(False)
        self._search.blockSignals(True)
        self._search.setText(state.search)
        self._search.blockSignals(False)
        self._filter_timer.stop()
        self._page_index = state.page_index
        self._apply(reset_page=False)
        self._grid.select_ids(set(state.selected))
        self._grid.set_cursor(state.cursor)
        if state.focused:
            self._grid.setFocus(Qt.FocusReason.OtherFocusReason)
        restore_scroll(self._scroll, state.scroll)

    def set_avatar(self, source_id: int, path: str):
        if int(source_id) == self.source_id:
            pixmap = load_pixmap(path, 120)
            if pixmap is not None:
                self._avatar.setImage(pixmap)

    def reload(self):
        """Re-read the source's items (download states change under us)."""

        self._stale = False
        if not self.source_id:
            return
        self._items = self._page._source_items(self.source_id)
        # Download-state changes land here: keep what the user selected and
        # where they were scrolled to, instead of jumping back to the top.
        self._apply(reset_page=False, keep_view=True)

    def refresh_status(self):
        """Re-read whether the author is still a local subscription."""

        if self.status_bar.isVisibleTo(self):
            self.status_bar.refresh_local()

    def reload_if_visible(self):
        if self.isVisible():
            self.reload()
        else:
            self._stale = True

    def showEvent(self, event):
        super().showEvent(event)
        if self._stale:
            self.reload()

    def _apply(self, *, reset_page: bool, keep_view: bool = False):
        mode = str(self._state_combo.currentData() or "all")
        order = str(self._sort_combo.currentData() or "date_desc")
        self._filtered = sort_items(filter_items(self._items, mode, self._search.text()), order)
        if reset_page:
            self._page_index = 0
        self._go(self._page_index, keep_view=keep_view)

    def _page_count(self) -> int:
        return max(1, -(-len(self._filtered) // GRID_PAGE_SIZE))

    def _go(self, index: int, *, keep_view: bool = False):
        bar = self._scroll.verticalScrollBar()
        selected_ids = {v.video_id for v in self._grid.selected_videos()} if keep_view else set()
        scroll_value = bar.value() if keep_view else 0
        self._page_index = max(0, min(index, self._page_count() - 1))
        start = self._page_index * GRID_PAGE_SIZE
        chunk = self._filtered[start:start + GRID_PAGE_SIZE]
        title = str(self._source.get("title", "") or "")
        is_author = str(self._source.get("source_type", "") or "") == "author"
        videos = [item_to_video(item, author_name=title if is_author else "") for item in chunk]
        for video in videos:
            cached = self._pixmaps.get(video.video_id) or load_pixmap(str(video.raw.get("_thumbnail_path", "") or ""))
            if cached is not None:
                self._pixmaps[video.video_id] = cached
                self._grid.set_cover(video.video_id, cached)
        self._grid.set_videos(videos)
        if selected_ids:
            self._grid.select_ids(selected_ids)
        bar.setValue(scroll_value)
        if scroll_value:
            # The scroll range may only grow once the rebuilt grid is laid out.
            QTimer.singleShot(0, bar, lambda: bar.setValue(scroll_value))
        self._request_covers(videos)
        self._prev_btn.setEnabled(self._page_index > 0)
        self._next_btn.setEnabled(self._page_index < self._page_count() - 1)
        self._page_label.setText(
            tr(
                f"Page {self._page_index + 1} / {self._page_count()}",
                f"第 {self._page_index + 1} / {self._page_count()} 页",
                f"{self._page_index + 1} / {self._page_count()} ページ",
            )
        )
        self._page_video_ids = [v.video_id for v in videos]
        self._sync_status()
        self._sync_buttons()

    def _sync_status(self):
        """Counts, plus how many covers of this page are still on their way."""

        new_count = sum(1 for item in self._items if item_state(item) == "new")
        text = tr(
            f"{len(self._filtered)} of {len(self._items)} videos · {new_count} new",
            f"显示 {len(self._filtered)} / {len(self._items)} 个视频 · {new_count} 个新增",
            f"{len(self._filtered)} / {len(self._items)} 本 · 新規 {new_count}",
        )
        missing = sum(1 for video_id in getattr(self, "_page_video_ids", []) if video_id not in self._pixmaps)
        if missing and self._covers.pending():
            text += tr(
                f" · loading covers ({missing} left)",
                f" · 正在加载封面（还剩 {missing} 个）",
                f" · カバー読み込み中（残り {missing}）",
            )
        elif missing:
            text += tr(
                f" · {missing} cover(s) unavailable — Refresh to retry",
                f" · {missing} 个封面暂未取到，点“刷新”重试",
                f" · カバー {missing} 件が未取得です。更新で再試行できます",
            )
        self._status.setText(text)

    def _request_covers(self, videos: list[SearchVideo]):
        requests = [
            (v.video_id, v.thumbnail_url)
            for v in videos
            if v.video_id not in self._pixmaps
        ]
        if requests:
            # What is on screen now goes first, ahead of the overview's strips.
            self._covers.request(requests, urgent=True)

    def _on_cover_ready(self, video_id: str, path: str):
        pixmap = load_pixmap(path)
        if pixmap is not None:
            self._pixmaps[video_id] = pixmap
            self._grid.set_cover(video_id, pixmap)
            self._sync_status()

    # ── actions ──────────────────────────────────────────────────────────────

    def _sync_buttons(self):
        selected = self._grid.selected_videos()
        self._selected_label.setText(
            tr(f"{len(selected)} selected", f"已选 {len(selected)} 个", f"{len(selected)} 件選択")
        )
        self._download_selected_btn.setEnabled(bool(selected))
        self._download_new_btn.setEnabled(bool(downloadable_ids(self._items, only_new=True)))
        self._download_all_btn.setEnabled(bool(downloadable_ids(self._filtered)))

    def _queue(self, ids: list[str]):
        self._rule_picker.apply_selected(show_notice=False)
        self._page._enqueue_ids(ids, rule_id=self._rule_picker.selected_rule_id())

    def _download_selected(self):
        ids = [v.video_id for v in self._grid.selected_videos()]
        self._queue(ids)
        self._grid.clear_selection()

    def _download_new(self):
        self._queue(downloadable_ids(self._items, only_new=True))

    def _download_all(self):
        self._queue(downloadable_ids(self._filtered))

    def _refresh_source(self):
        if self.source_id:
            self._covers.forget([item["video_id"] for item in self._items if item.get("video_id")])
            self._page._refresh_source_ids_from_view(self.source_id)

    def _open_source_page(self):
        self._page._open_source_page(self._source)

    def _on_card_context(self, video: SearchVideo, pos: QPoint):
        selected = [v.video_id for v in self._grid.selected_videos()]
        self._page._show_video_menu(video, pos, selected if video.video_id in selected else None)
