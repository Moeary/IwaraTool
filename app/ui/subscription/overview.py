"""Overview of all subscription sources, one row per author or playlist.

Rows are realized lazily as they scroll into view; opening a row shows
that source as a poster grid (see source_items_view)."""

from __future__ import annotations

import time
from typing import Any

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QVBoxLayout, QWidget

from qfluentwidgets import (
    CaptionLabel,
    ComboBox,
    FluentIcon,
    PrimaryPushButton,
    PushButton,
    SearchLineEdit,
    ToolButton,
)

from ...config import app_config
from ...i18n import tr
from ...signal_bus import signal_bus
from ..chrome import EmptyState
from ..media.card_size import CardSizeControl, saved_card_width
from ..media.pixmaps import PixmapLRU
from ..media.grid import transparent_scroll_area
from .helpers import _source_search_text, _source_sort_key, _source_sort_label
from ..theme import PAGE_SPACING, set_secondary_text, summary_text
from ..flow_layout import ResponsiveFlowLayout
from .cover_loader import CoverLoader, load_pixmap
from .item_queries import is_feed_source
from .source_row import SourceRow
from .overview_rows import OverviewRowsMixin


RECENT_PER_SOURCE = 8  # cover strip length


OVERVIEW_SORT_FIELDS = (
    "title",
    "new_count",
    "undownloaded_count",
    "item_count",
    "last_checked_at",
    "created_at",
)


class SubscriptionOverview(OverviewRowsMixin, QWidget):
    """Large rows, one per subscription, with a toolbar for finding and adding."""

    source_opened = Signal(int)

    def __init__(self, page, covers: CoverLoader, parent: QWidget | None = None):
        super().__init__(parent)
        self._page = page
        self._covers = covers
        self._init_state()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(PAGE_SPACING)
        root.addLayout(self._build_toolbar(page))

        self._summary = CaptionLabel("", self)
        set_secondary_text(self._summary)
        root.addWidget(self._summary)
        self._build_row_list(root, page)
        self._init_timers()

        covers.cover_ready.connect(self._on_cover_ready)
        signal_bus.media_card_size_changed.connect(self._on_card_size)

    def _init_state(self):
        self._rows: dict[int, SourceRow] = {}  # the subscriptions that currently have a real row
        self._slots: dict[int, QWidget] = {}  # what fills each subscription's place: a row or a spacer
        self._heights: dict[int, int] = {}  # measured row heights, kept after a row is torn down
        self._data: dict[int, tuple] = {}  # source, recent items, signature of every listed subscription
        self._order: dict[int, int] = {}
        self._tiles_stale = False
        self._realize_timer = QTimer(self)
        self._realize_timer.setSingleShot(True)
        self._realize_timer.setInterval(60)
        self._realize_timer.timeout.connect(self._realize)
        self._todo: list = []
        self._scroll_target = 0
        self._build_timer = QTimer(self)
        self._build_timer.setSingleShot(True)
        self._build_timer.timeout.connect(lambda: self._build_rows(sliced=True))
        self._pixmaps = PixmapLRU()
        self._dirty = True
        self._tile_min = int(saved_card_width() * 0.8)
        field = str(app_config.get_ui_value("subscription_overview_sort_v1", "title") or "title")
        self._sort_field = field if field in OVERVIEW_SORT_FIELDS else "title"
        self._sort_desc = str(app_config.get_ui_value("subscription_overview_desc_v1", "0")) in {"1", "true", "True"}

    def _build_row_list(self, root: QVBoxLayout, page):
        self._scroll = transparent_scroll_area("SubscriptionOverviewScroll", self)
        holder = QWidget()
        self._column = QVBoxLayout(holder)
        self._column.setContentsMargins(0, 0, 8, 24)
        self._column.setSpacing(12)
        self._column.addStretch(1)
        self._scroll.setWidget(holder)
        root.addWidget(self._scroll, 1)

        self._empty = EmptyState(
            FluentIcon.PEOPLE, "", "", self, action_text=tr("Add author / playlist", "添加作者 / 播放列表", "作者/リストを追加"),
        )
        self._empty.action_clicked.connect(page._add_source)
        root.addWidget(self._empty, 1)
        self._empty.hide()

    def _init_timers(self):
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(180)
        self._filter_timer.timeout.connect(self.reload)
        self._cover_timer = QTimer(self)
        self._cover_timer.setSingleShot(True)
        self._cover_timer.setInterval(120)
        self._cover_timer.timeout.connect(self._request_visible_covers)
        self._scroll.verticalScrollBar().valueChanged.connect(lambda _v: (self._cover_timer.start(), self._realize_timer.start()))

    def _build_toolbar(self, page) -> ResponsiveFlowLayout:
        bar = ResponsiveFlowLayout(spacing=8)
        self._search = SearchLineEdit(self)
        self._search.setPlaceholderText(tr("Filter authors and playlists…", "筛选作者 / 播放列表…", "作者・リストを絞り込み…"))
        self._search.setClearButtonEnabled(True)
        self._search.setMinimumWidth(220)
        self._search.textChanged.connect(lambda _text: self._filter_timer.start())
        bar.addWidget(self._search)

        self._type_combo = ComboBox(self)
        for label, data in (
            (tr("All", "全部", "すべて"), ""),
            (tr("Authors", "作者", "作者"), "author"),
            (tr("Playlists", "播放列表", "プレイリスト"), "playlist"),
        ):
            self._type_combo.addItem(label)
            self._type_combo.setItemData(self._type_combo.count() - 1, data)
        self._type_combo.setFixedWidth(116)
        self._type_combo.currentIndexChanged.connect(lambda _i: self.reload())
        bar.addWidget(self._type_combo)

        self._sort_combo = ComboBox(self)
        for field_name in OVERVIEW_SORT_FIELDS:
            self._sort_combo.addItem(_source_sort_label(field_name))
            self._sort_combo.setItemData(self._sort_combo.count() - 1, field_name)
        self._sort_combo.setCurrentIndex(OVERVIEW_SORT_FIELDS.index(self._sort_field))
        self._sort_combo.setFixedWidth(132)
        self._sort_combo.setToolTip(tr("Sort by", "排序字段", "並び替え項目"))
        self._sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        bar.addWidget(self._sort_combo)
        self._sort_dir = ToolButton(self)
        self._sort_dir.clicked.connect(self._toggle_direction)
        bar.addWidget(self._sort_dir)
        self._sync_direction_button()

        bar.addWidget(CardSizeControl(self))

        add_btn = PrimaryPushButton(tr("Add author / playlist", "添加作者 / 播放列表", "作者/リストを追加"), self, FluentIcon.ADD)
        add_btn.clicked.connect(page._add_source)
        bar.addWidget(add_btn)
        import_btn = PushButton(tr("Import followed", "导入关注作者", "フォローを取込"), self, FluentIcon.PEOPLE)
        import_btn.clicked.connect(page._import_followed_authors)
        bar.addWidget(import_btn)
        self.refresh_all_btn = PushButton(tr("Refresh all", "全部刷新", "全件更新"), self, FluentIcon.SYNC)
        self.refresh_all_btn.clicked.connect(page._refresh_all)
        bar.addWidget(self.refresh_all_btn)
        return bar

    # ── data ─────────────────────────────────────────────────────────────────

    def mark_dirty(self):
        self._dirty = True

    def reload_if_dirty(self):
        if self._dirty:
            self.reload()

    def visible_sources(self) -> list[dict[str, Any]]:
        sources = [s for s in self._page._all_sources if not is_feed_source(s)]
        wanted = str(self._type_combo.currentData() or "")
        if wanted:
            sources = [s for s in sources if str(s.get("source_type", "") or "") == wanted]
        query = self._search.text().strip().casefold()
        if query:
            sources = [s for s in sources if query in _source_search_text(s)]
        sources.sort(key=lambda s: _source_sort_key(s, self._sort_field), reverse=self._sort_desc)
        return sources

    # A row is a whole styled widget tree (about 1 MB), and a library can have
    # hundreds of subscriptions.  Only the rows near the viewport are real
    # widgets; every other subscription is a bare spacer of the same height.
    # Rows are built in time slices as they come into range and torn down
    # again once they are far away, so memory and start-up cost follow what is
    # on screen instead of the size of the library.
    SYNC_ROWS = 6  # up to this many new rows are built at once
    SLICE_SECONDS = 0.012
    ROW_ESTIMATE = 250  # px, until a real row has been measured
    ROW_SPACING = 12
    BUILD_MARGIN = 1.0  # viewport heights above and below that get real rows
    KEEP_MARGIN = 2.5  # beyond this many viewport heights a row is torn down
    MIN_VIEWPORT = 600  # px; a hidden page has no real viewport yet

    _SIGNATURE_FIELDS = (
        "id", "title", "source_key", "source_type", "enabled", "new_count", "item_count",
        "undownloaded_count", "last_checked_at", "source_origin",
    )

    def reload(self):
        self._dirty = False
        self._build_timer.stop()
        sources = self.visible_sources()
        recent = self._page._recent_items(RECENT_PER_SOURCE)
        scroll_value = self._scroll.verticalScrollBar().value()
        self._order = {int(s.get("id", 0) or 0): index for index, s in enumerate(sources)}
        for source_id in [sid for sid in self._slots if sid not in self._order]:
            self._drop_slot(source_id)
        self._todo = []
        for source in sources:
            source_id = int(source.get("id", 0) or 0)
            items = recent.get(source_id, [])
            signature = self._row_signature(source, items)
            self._data[source_id] = (source, items, signature)
            row = self._rows.get(source_id)
            if row is not None and getattr(row, "signature", None) == signature:
                avatar = str(source.get("avatar_path", "") or "")
                if avatar and avatar != row.avatar_path:
                    row.set_avatar(avatar)
            elif row is not None:
                # Changed data: the row is rebuilt below if it is still near the viewport.
                self._heights[source_id] = max(row.height(), row.sizeHint().height())
                self._rows.pop(source_id, None)
                self._replace_slot(source_id, self._new_spacer(source_id), keep_view=False)
            elif source_id not in self._slots:
                self._replace_slot(source_id, self._new_spacer(source_id), keep_view=False)
        total_new = sum(int(s.get("new_count", 0) or 0) for s in sources)
        total_missing = sum(int(s.get("undownloaded_count", 0) or 0) for s in sources)
        self._summary.setText(
            summary_text([
                (tr("Subscriptions", "订阅源", "購読元"), len(sources)),
                (tr("New", "新增", "新規"), total_new),
                (tr("Missing", "未下载", "未保存"), total_missing),
            ])
        )
        has_any = bool(self._page._all_sources) and any(not is_feed_source(s) for s in self._page._all_sources)
        self._scroll.setVisible(bool(sources))
        self._empty.setVisible(not sources)
        if not sources:
            if has_any:
                self._empty.set_text(tr("No match", "没有符合条件的订阅源", "該当する購読元がありません"))
            else:
                self._empty.set_text(
                    tr("No subscriptions yet", "还没有订阅", "購読はまだありません"),
                    tr(
                        "Add an author or playlist, or subscribe from a video's page or an author page.",
                        "添加作者或播放列表，也可以在视频详情页或作者页里订阅作者。",
                        "作者やプレイリストを追加するか、動画ページ・作者ページから購読できます。",
                    ),
                )
            if self._empty.button is not None:
                self._empty.button.setVisible(not has_any)
        self._scroll_target = scroll_value
        self._sync_order()
        self._realize()

    def _build_rows(self, *, sliced: bool):
        """Create queued rows; when ``sliced`` stop after ~12 ms and continue next tick."""

        start = time.perf_counter()
        while self._todo:
            source_id, source, items, signature = self._todo.pop(0)
            if source_id not in self._order or source_id in self._rows:
                continue
            row = self._make_row(source, items, signature)
            self._rows[source_id] = row
            self._replace_slot(source_id, row)
            self._heights[source_id] = row.sizeHint().height()
            if sliced and self._todo and time.perf_counter() - start > self.SLICE_SECONDS:
                self._build_timer.start(0)
                return
        self._sync_order()
        QTimer.singleShot(0, lambda: self._restore_scroll(self._scroll_target))
        self._cover_timer.start()

    def building(self) -> bool:
        return bool(self._todo)

    def _restore_scroll(self, value: int):
        try:
            self._scroll.verticalScrollBar().setValue(value)
        except RuntimeError:
            pass  # the page was closed before the deferred call ran

    def set_avatar(self, source_id: int, path: str):
        row = self._rows.get(int(source_id))
        if row is not None:
            row.set_avatar(path)

    # ── toolbar ──────────────────────────────────────────────────────────────

    def _on_sort_changed(self, index: int):
        field = str(self._sort_combo.itemData(index) or "title")
        self._sort_field = field if field in OVERVIEW_SORT_FIELDS else "title"
        app_config.set_ui_value("subscription_overview_sort_v1", self._sort_field)
        self.reload()

    def _toggle_direction(self):
        self._sort_desc = not self._sort_desc
        app_config.set_ui_value("subscription_overview_desc_v1", "1" if self._sort_desc else "0")
        self._sync_direction_button()
        self.reload()

    def _sync_direction_button(self):
        self._sort_dir.setIcon(FluentIcon.DOWN if self._sort_desc else FluentIcon.UP)
        self._sort_dir.setToolTip(
            tr("Descending order", "倒序排列", "降順") if self._sort_desc else tr("Ascending order", "正序排列", "昇順")
        )

    def _on_card_size(self, width: int):
        self._tile_min = max(120, int(width * 0.8))
        if not self.isVisible():
            self._tiles_stale = True  # applied on show; 65 rows of strips are not worth relaying out unseen
            return
        self._apply_tile_size()

    def _apply_tile_size(self):
        self._tiles_stale = False
        for row in self._rows.values():
            row.set_tile_min_width(self._tile_min)
        # Strip heights follow the tile size: forget the old measurements and re-measure.
        self._heights.clear()
        QTimer.singleShot(0, self._remeasure)
        self._cover_timer.start()

    def _remeasure(self):
        for source_id, row in list(self._rows.items()):
            self._heights[source_id] = row.sizeHint().height()
        for source_id, widget in self._slots.items():
            if source_id not in self._rows:
                widget.setFixedHeight(self._slot_height(source_id))
        self._realize_timer.start()

    # ── covers ───────────────────────────────────────────────────────────────

    def _start_cover_requests(self, requests: list[tuple[str, str]]):
        self._covers.request(requests)

    def _request_visible_covers(self):
        if not self.isVisible():
            return
        bar = self._scroll.verticalScrollBar()
        top = bar.value() - 400
        bottom = bar.value() + self._scroll.viewport().height() + 400
        requests: list[tuple[str, str]] = []
        for row in self._rows.values():
            if row.y() + row.height() < top or row.y() > bottom or row.strip is None:
                continue
            for card in row.strip.shown_cards():
                video = card.video
                cached = self._pixmaps.get(video.video_id)
                if cached is None:
                    path = str(video.raw.get("_thumbnail_path", "") or "") or self._page._cover_path(
                        video.video_id, video.thumbnail_url,
                    )
                    cached = load_pixmap(path)
                    if cached is not None:
                        self._pixmaps[video.video_id] = cached
                if cached is not None:
                    card.set_cover(cached)
                elif video.video_id:
                    requests.append((video.video_id, video.thumbnail_url))
        if requests:
            self._start_cover_requests(requests)

    def _on_cover_ready(self, video_id: str, path: str):
        pixmap = load_pixmap(path)
        if pixmap is None:
            return
        self._pixmaps[video_id] = pixmap
        for row in self._rows.values():
            if row.strip is None:
                continue
            for card in row.strip.cards:
                if card.video.video_id == video_id:
                    card.set_cover(pixmap)

    def showEvent(self, event):
        super().showEvent(event)
        if self._tiles_stale:
            self._apply_tile_size()
        self.reload_if_dirty()
        self._cover_timer.start()
        self._realize_timer.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        # Whatever the user opens next matters more than these strips.
        self._covers.drop_background()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._cover_timer.start()
        self._realize_timer.start()
