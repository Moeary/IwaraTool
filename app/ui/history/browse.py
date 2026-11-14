"""Ways into the history: list / grid / cover views, recent, unwatched and by author."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from PySide6.QtCore import QCollator, QLocale, Qt
from PySide6.QtWidgets import QAbstractItemView, QListWidgetItem, QVBoxLayout, QWidget

from qfluentwidgets import ComboBox, FluentIcon, ListWidget, SegmentedWidget, ToolButton

from ...config import app_config
from ...i18n import current_language, tr
from ...signal_bus import signal_bus
from ..flow_layout import ResponsiveFlowLayout
from ..media.card_size import CardSizeControl
from .cover_grid import MODE_COVER, MODE_GRID, HistoryCoverGrid


VIEW_LIST = "list"
SCOPE_ALL = "all"
SCOPE_RECENT = "recent"
SCOPE_UNWATCHED = "unwatched"
SCOPE_AUTHOR = "author"
RECENT_DAYS = 30
AUTHOR_SORT_COUNT = "count"  # most videos first
AUTHOR_SORT_NAME = "name"  # A → Z in the UI language's order (pinyin for Chinese)

_VIEW_KEY = "history_view_mode_v1"
_SCOPE_KEY = "history_scope_v1"
_AUTHOR_SORT_KEY = "history_author_sort_v1"
_COLLATOR_LOCALES = {"zh": "zh_CN", "ja": "ja_JP", "en": "en_US"}


def author_name_collator() -> QCollator:
    collator = QCollator(QLocale(_COLLATOR_LOCALES.get(current_language(), "en_US")))
    collator.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
    collator.setNumericMode(True)  # "2B" before "10A"
    return collator


def sort_authors(counts: dict[str, int], order: str) -> list[tuple[str, int]]:
    """Named authors with their record counts, by count or by name."""

    named = [(name, n) for name, n in counts.items() if name]
    collator = author_name_collator()
    if order == AUTHOR_SORT_NAME:
        return sorted(named, key=lambda pair: (collator.sortKey(pair[0]), pair[0]))
    return sorted(named, key=lambda pair: (-pair[1], collator.sortKey(pair[0]), pair[0]))


class HistoryBrowseMixin:
    """Ways into the history: list / grid / cover views, recent, unwatched and by author."""

    def _init_browse_state(self):
        view = str(app_config.get_ui_value(_VIEW_KEY, VIEW_LIST) or VIEW_LIST)
        scope = str(app_config.get_ui_value(_SCOPE_KEY, SCOPE_ALL) or SCOPE_ALL)
        self._view_mode = view if view in (VIEW_LIST, MODE_GRID, MODE_COVER) else VIEW_LIST
        self._scope = scope if scope in (SCOPE_ALL, SCOPE_RECENT, SCOPE_UNWATCHED, SCOPE_AUTHOR) else SCOPE_ALL
        self._author_filter = ""  # "" = every author
        author_sort = str(app_config.get_ui_value(_AUTHOR_SORT_KEY, AUTHOR_SORT_COUNT) or AUTHOR_SORT_COUNT)
        self._author_sort = author_sort if author_sort in (AUTHOR_SORT_COUNT, AUTHOR_SORT_NAME) else AUTHOR_SORT_COUNT
        self._table_stale = False  # the table was skipped while a card view was shown

    def _build_browse_row(self) -> ResponsiveFlowLayout:
        row = ResponsiveFlowLayout()
        row.setSpacing(8)
        self._scope_bar = SegmentedWidget(self)
        for key, text, tip in (
            (SCOPE_ALL, tr("All", "全部", "すべて"), ""),
            (SCOPE_RECENT, tr("Recently downloaded", "最近下载", "最近の保存"), tr(
                f"Downloaded in the last {RECENT_DAYS} days", f"最近 {RECENT_DAYS} 天内下载的作品", f"直近 {RECENT_DAYS} 日に保存",
            )),
            (SCOPE_UNWATCHED, tr("Unwatched", "未观看", "未視聴"), tr(
                "Files on disk that were never played from IwaraTool",
                "文件仍在、但从未在 IwaraTool 中播放过的作品",
                "IwaraTool で再生したことのない保存済み動画",
            )),
            (SCOPE_AUTHOR, tr("By author", "按作者", "作者別"), ""),
        ):
            # itemClicked passes a bool: keep it away from ``key``.
            self._scope_bar.addItem(key, text, lambda _checked=False, key=key: self._set_scope(key))
            if tip:
                self._scope_bar.items[key].setToolTip(tip)
        self._scope_bar.setCurrentItem(self._scope)
        row.addWidget(self._scope_bar)

        self._grid_sort_combo = ComboBox(self)
        for text, column in self._grid_sort_choices():
            self._grid_sort_combo.addItem(text, userData=column)
        self._grid_sort_combo.setFixedWidth(150)
        self._grid_sort_combo.currentIndexChanged.connect(self._on_grid_sort_changed)
        row.addWidget(self._grid_sort_combo)
        self._grid_sort_dir_btn = ToolButton(FluentIcon.DOWN, self)
        self._grid_sort_dir_btn.setToolTip(tr("Sort direction", "排序方向", "並び順"))
        self._grid_sort_dir_btn.clicked.connect(self._toggle_grid_sort_direction)
        row.addWidget(self._grid_sort_dir_btn)

        self._view_bar = SegmentedWidget(self)
        for key, text in (
            (VIEW_LIST, tr("List", "列表", "リスト")),
            (MODE_GRID, tr("Grid", "方格", "グリッド")),
            (MODE_COVER, tr("Covers", "封面", "カバー")),
        ):
            self._view_bar.addItem(key, text, lambda _checked=False, key=key: self._set_view_mode(key))
        self._view_bar.setCurrentItem(self._view_mode)
        row.addWidget(self._view_bar)
        self._card_size_control = CardSizeControl(self)
        row.addWidget(self._card_size_control)
        return row

    def _grid_sort_choices(self) -> list[tuple[str, int]]:
        return [
            (tr("Downloaded at", "下载时间", "保存日時"), self._COL_DOWNLOADED),
            (tr("Published", "发布日期", "公開日"), self._COL_PUBLISHED),
            (tr("Title", "标题", "タイトル"), self._COL_TITLE),
            (tr("Author", "作者", "作者"), self._COL_AUTHOR),
            (tr("Likes", "点赞", "いいね"), self._COL_LIKES),
            (tr("Views", "播放", "再生"), self._COL_VIEWS),
        ]

    def _build_author_panel(self, parent: QWidget) -> QWidget:
        self._author_panel = QWidget(parent)
        self._author_panel.setMinimumWidth(180)
        self._author_panel.setMaximumWidth(320)
        layout = QVBoxLayout(self._author_panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self._author_sort_bar = SegmentedWidget(self._author_panel)
        for key, text, tip in (
            (AUTHOR_SORT_COUNT, tr("Most videos", "作品数", "作品数"), tr(
                "Authors with the most downloads first", "下载作品多的作者在前", "保存数の多い作者から",
            )),
            (AUTHOR_SORT_NAME, tr("Name", "作者名", "名前"), tr(
                "Authors by name, A → Z", "按作者名排序（中文按拼音）", "作者名順",
            )),
        ):
            self._author_sort_bar.addItem(key, text, lambda _checked=False, key=key: self._set_author_sort(key))
            self._author_sort_bar.items[key].setToolTip(tip)
        self._author_sort_bar.setCurrentItem(self._author_sort)
        layout.addWidget(self._author_sort_bar)
        self._author_list = ListWidget(self._author_panel)
        self._author_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._author_list.currentItemChanged.connect(self._on_author_chosen)
        layout.addWidget(self._author_list, 1)
        return self._author_panel

    def _build_cover_grid(self, parent: QWidget) -> HistoryCoverGrid:
        self._cover_grid = HistoryCoverGrid(parent)
        self._cover_grid.activated.connect(self._play_record)
        self._cover_grid.context_requested.connect(self._show_grid_context_menu)
        self._cover_grid.itemSelectionChanged.connect(self._update_action_state)
        return self._cover_grid

    # ── state changes ────────────────────────────────────────────────────────

    def _set_scope(self, scope: str):
        if scope not in (SCOPE_ALL, SCOPE_RECENT, SCOPE_UNWATCHED, SCOPE_AUTHOR) or scope == self._scope:
            return
        self._scope = scope
        app_config.set_ui_value(_SCOPE_KEY, scope)
        self._sync_browse_controls()
        self._apply_filters()

    def _set_author_sort(self, order: str):
        if order not in (AUTHOR_SORT_COUNT, AUTHOR_SORT_NAME) or order == self._author_sort:
            return
        self._author_sort = order
        app_config.set_ui_value(_AUTHOR_SORT_KEY, order)
        self._author_sort_bar.setCurrentItem(order)
        self._rebuild_author_list()
        current = self._author_list.currentItem()
        if current is not None:
            self._author_list.scrollToItem(current)

    def _set_view_mode(self, mode: str):
        if mode not in (VIEW_LIST, MODE_GRID, MODE_COVER) or mode == self._view_mode:
            return
        self._view_mode = mode
        app_config.set_ui_value(_VIEW_KEY, mode)
        self._sync_browse_controls()
        self._render_current_view(getattr(self, "_visible_records", []), switching=True)

    def _sync_browse_controls(self):
        cards = self._view_mode != VIEW_LIST
        self._view_bar.setCurrentItem(self._view_mode)
        self._scope_bar.setCurrentItem(self._scope)
        self._view_stack.setCurrentWidget(self._cover_grid if cards else self._table)
        self._grid_sort_combo.setVisible(cards)
        self._grid_sort_dir_btn.setVisible(cards)
        self._card_size_control.setVisible(cards)
        self._author_panel.setVisible(self._scope == SCOPE_AUTHOR)
        index = next(
            (i for i, (_text, column) in enumerate(self._grid_sort_choices()) if column == self._sort_column), -1,
        )
        if index >= 0 and index != self._grid_sort_combo.currentIndex():
            self._grid_sort_combo.blockSignals(True)
            self._grid_sort_combo.setCurrentIndex(index)
            self._grid_sort_combo.blockSignals(False)
        self._grid_sort_dir_btn.setIcon(FluentIcon.DOWN if self._sort_reverse else FluentIcon.UP)

    def _on_grid_sort_changed(self, index: int):
        column = self._grid_sort_combo.itemData(index)
        if column is None or column == self._sort_column:
            return
        self._sort_column = int(column)
        self._sort_reverse = self._sort_column in (
            self._COL_PUBLISHED, self._COL_LIKES, self._COL_VIEWS, self._COL_DOWNLOADED,
        )
        self._restore_sort_indicator()
        self._sync_browse_controls()
        self._apply_filters()

    def _toggle_grid_sort_direction(self):
        self._sort_reverse = not self._sort_reverse
        self._restore_sort_indicator()
        self._sync_browse_controls()
        self._apply_filters()

    def _on_author_chosen(self, current: QListWidgetItem | None, _previous=None):
        author = str(current.data(Qt.ItemDataRole.UserRole) or "") if current is not None else ""
        if author != self._author_filter:
            self._author_filter = author
            self._apply_filters()

    # ── filtering ────────────────────────────────────────────────────────────

    def _scope_accepts(self, record: dict[str, Any], state_key: str, recent_cutoff: str) -> bool:
        if self._scope == SCOPE_RECENT:
            return str(record.get("downloaded_at", "") or "") >= recent_cutoff
        if self._scope == SCOPE_UNWATCHED:
            return state_key == "ok" and not str(record.get("watched_at", "") or "")
        if self._scope == SCOPE_AUTHOR and self._author_filter:
            return str(record.get("author", "") or "") == self._author_filter
        return True

    @staticmethod
    def _recent_cutoff() -> str:
        # downloaded_at is SQLite's CURRENT_TIMESTAMP (UTC, "YYYY-MM-DD HH:MM:SS").
        return (datetime.now(timezone.utc) - timedelta(days=RECENT_DAYS)).strftime("%Y-%m-%d %H:%M:%S")

    def _rebuild_author_list(self):
        counts = Counter(str(r.get("author", "") or "") for r in self._all_records)
        authors = sort_authors(counts, self._author_sort)
        self._author_list.blockSignals(True)
        try:
            self._author_list.clear()
            everyone = QListWidgetItem(tr(f"All authors ({len(self._all_records)})", f"全部作者（{len(self._all_records)}）", f"すべての作者（{len(self._all_records)}）"))
            everyone.setData(Qt.ItemDataRole.UserRole, "")
            self._author_list.addItem(everyone)
            current = everyone
            for name, count in authors:
                item = QListWidgetItem(f"{name}  ({count})")
                item.setData(Qt.ItemDataRole.UserRole, name)
                item.setToolTip(name)
                self._author_list.addItem(item)
                if name == self._author_filter:
                    current = item
            self._author_list.setCurrentItem(current)
            if current is everyone:
                self._author_filter = ""
        finally:
            self._author_list.blockSignals(False)

    # ── rendering ────────────────────────────────────────────────────────────

    def _render_current_view(self, records: list[dict[str, Any]], *, switching: bool = False):
        if self._view_mode == VIEW_LIST:
            if not switching or self._table_stale:
                self._table_stale = False
                self._render_table(records)
            return
        # The table is only rebuilt when it is shown again.
        self._table_stale = True
        self._cover_grid.set_mode(self._view_mode)
        self._cover_grid.set_records(records, file_ok=lambda record: self._record_state(record)[1] == "ok")
        self._state_memo = None  # the pass is over; later checks look at the disk again
        self._update_action_state()

    def _show_grid_context_menu(self, position):
        if not self._cover_grid.select_at(position):
            return
        self._exec_record_menu(self._cover_grid.viewport().mapToGlobal(position))

    def _play_record(self, video_id: str):
        title = str(self._records_by_id.get(video_id, {}).get("title", "") or "")
        signal_bus.video_preview_requested.emit(video_id, title, "")
