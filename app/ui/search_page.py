"""Fluent search interface for videos, authors, tags, and playlists."""

from __future__ import annotations

import json
import webbrowser
from typing import Any

from PySide6.QtCore import QPoint, QThread, Qt, QSize, QTimer
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHeaderView,
    QHBoxLayout,
    QListWidgetItem,
    QSizePolicy,
    QStackedWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    ComboBox,
    FluentIcon,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    ListWidget,
    PrimaryPushButton,
    PushButton,
    SubtitleLabel,
    TableWidget,
    TitleLabel,
    ToolButton,
    isDarkTheme,
)

from ..config import app_config
from ..core.manager import download_manager
from ..core.oreno3d_search import (
    apply_tag_suggestion,
    tag_suggestion_query,
)
from ..core.search import (
    SearchAuthor,
    SearchFilters,
    SearchPageResult,
    SearchVideo,
    normalize_video,
)
from ..i18n import tr
from ..signal_bus import signal_bus
from .rules_page import RulePicker
from .search_actions import SearchActionsMixin
from .search_widgets import (
    SearchHistoryPopup,
    SearchKeywordEdit,
    TagSuggestionPopup,
    _author_source_info,
    _author_subscription_target,
    _decode_search_history,
    _format_count,
    _format_duration,
    _grid_text_height,
    _normalize_search_history_entry,
    _oreno3d_video_url,
    _search_grid_style,
    _short_text,
    _upsert_search_history,
)
from .search_workers import (
    DEFAULT_COVER_DOWNLOAD_CONCURRENCY as _DEFAULT_COVER_DOWNLOAD_CONCURRENCY,
    DEFAULT_SEARCH_RESOLUTION_CONCURRENCY as _DEFAULT_SEARCH_RESOLUTION_CONCURRENCY,
    MAX_COVER_DOWNLOAD_CONCURRENCY as _MAX_COVER_DOWNLOAD_CONCURRENCY,
    MAX_SEARCH_RESOLUTION_CONCURRENCY as _MAX_SEARCH_RESOLUTION_CONCURRENCY,
    SearchImageWorker,
    SearchIwaraAuthorWorker,
    SearchOrenoAuthorWorker,
    SearchOrenoLinkWorker,
    SearchQueueResolveWorker,
    SearchWorker,
)
from .ui_state import (
    connect_table_column_saver,
    connect_table_width_saver,
    open_table_column_dialog,
    restore_table_columns,
    restore_table_widths,
    show_fluent_text_input,
)
from .worker_lifecycle import stop_qthreads


_VIDEO_ICON_SIZE = QSize(260, 146)
_DEFAULT_GRID_HEIGHT = 238
_DEFAULT_GRID_COLUMNS = 4
_MAX_GRID_COLUMNS = 8
_SEARCH_HISTORY_KEY = "search_history_v1"


class SearchInterface(SearchActionsMixin, QWidget):
    """Search page with Fluent controls, cached covers, and a configurable list."""

    _DATA_ROLE = Qt.ItemDataRole.UserRole
    _RESULT_COLUMN_KEYS = (
        "iwara_id",
        "title",
        "author",
        "views",
        "likes",
        "comments",
        "duration",
        "published",
        "tags",
        "iwara_url",
        "source_url",
        "source",
    )

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("SearchInterface")
        self._auto_search_ready = False
        self._search_controls_collapsed = False
        self._loading = False
        self._auto_search_timer = QTimer(self)
        self._auto_search_timer.setSingleShot(True)
        self._auto_search_timer.setInterval(120)
        self._auto_search_timer.timeout.connect(self._auto_start_search)
        self._generation = 0
        self._current_page = 0
        self._last_page: int | None = None
        self._next_page: int | None = None
        self._total: int | None = None
        self._all_videos: list[SearchVideo] = []
        self._all_authors: list[SearchAuthor] = []
        self._item_by_key: dict[str, QListWidgetItem] = {}
        self._image_path_by_key: dict[str, str] = {}
        self._search_workers: list[SearchWorker] = []
        self._image_workers: list[SearchImageWorker] = []
        self._image_pending_keys: set[str] = set()
        self._grid_resize_pending = False
        self._oreno_link_workers: list[SearchOrenoLinkWorker] = []
        self._iwara_author_workers: list[SearchIwaraAuthorWorker] = []
        self._oreno_author_workers: list[SearchOrenoAuthorWorker] = []
        self._queue_resolve_worker: SearchQueueResolveWorker | None = None
        self._queue_resolve_rule_id = ""
        self._tag_popup: TagSuggestionPopup | None = None
        self._active_tag_edit: LineEdit | None = None
        self._pending_open_video_ids: set[str] = set()
        self._pending_open_author_video_ids: set[str] = set()
        self._pending_author_subscription_video_ids: set[str] = set()
        self._build_ui()
        self._auto_search_ready = True

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(36, 24, 36, 18)
        root.setSpacing(12)

        title_row = QHBoxLayout()
        title_row.addWidget(TitleLabel(tr("Search", "搜索", "検索"), self))
        title_row.addStretch()
        self._source_status_label = BodyLabel("", self)
        title_row.addWidget(self._source_status_label)
        self._toggle_search_controls_btn = PushButton(self)
        self._toggle_search_controls_btn.clicked.connect(self._toggle_search_controls)
        title_row.addWidget(self._toggle_search_controls_btn)
        root.addLayout(title_row)

        query_card = CardWidget(self)
        query_layout = QVBoxLayout(query_card)
        query_layout.setContentsMargins(16, 14, 16, 14)
        query_layout.setSpacing(10)

        query_row = QHBoxLayout()
        query_row.setSpacing(8)
        query_row.addWidget(BodyLabel(tr("Source", "数据源", "ソース"), query_card))
        self._source_combo = ComboBox(query_card)
        self._add_combo_item(
            self._source_combo,
            tr("Oreno3D online search", "Oreno3D 在线搜索", "Oreno3Dオンライン検索"),
            "oreno3d",
        )
        self._add_combo_item(
            self._source_combo,
            tr("Iwara live API", "Iwara 实时 API", "IwaraライブAPI"),
            "iwara",
        )
        self._source_combo.setMinimumWidth(178)
        self._source_combo.currentIndexChanged.connect(self._on_source_changed)
        query_row.addWidget(self._source_combo)
        query_row.addWidget(BodyLabel(tr("Scope", "搜索类型", "検索対象"), query_card))
        self._scope_combo = ComboBox(query_card)
        self._scope_items = [
            (tr("Videos", "视频", "動画"), "videos"),
            (tr("Authors", "作者", "作者"), "authors"),
            (tr("Tags", "标签", "タグ"), "tags"),
            (tr("Playlists", "播放列表", "プレイリスト"), "playlists"),
        ]
        for text, data in self._scope_items:
            self._add_combo_item(self._scope_combo, text, data)
        self._scope_combo.setMinimumWidth(132)
        self._scope_combo.currentIndexChanged.connect(self._on_scope_changed)
        query_row.addWidget(self._scope_combo)

        query_row.addWidget(BodyLabel(tr("Sort", "排序", "並び順"), query_card))
        self._sort_combo = self._make_combo(
            [
                (tr("Newest", "最新", "新着"), "date"),
                (tr("Trending", "趋势", "トレンド"), "trending"),
                (tr("Popularity", "热度", "人気"), "popularity"),
                (tr("Most viewed", "最多人观看", "再生数最多"), "views"),
                (tr("Most liked", "喜欢最多", "いいね順"), "likes"),
            ],
            query_card,
        )
        self._sort_combo.setMinimumWidth(132)
        self._sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        query_row.addWidget(self._sort_combo)

        self._keyword_edit = SearchKeywordEdit(query_card)
        self._keyword_edit.setClearButtonEnabled(True)
        self._keyword_edit.setPlaceholderText(
            tr(
                "Keywords, tags, author username, or playlist ID…",
                "输入关键词、标签、作者用户名或播放列表 ID…",
                "キーワード、タグ、作者名、プレイリストID…",
            )
        )
        self._keyword_edit.returnPressed.connect(self._start_search)
        query_row.addWidget(self._keyword_edit, 1)

        self._search_btn = PrimaryPushButton(tr("Search", "搜索", "検索"), query_card, FluentIcon.SEARCH)
        self._search_btn.setMinimumWidth(112)
        self._search_btn.clicked.connect(self._start_search)
        query_row.addWidget(self._search_btn)

        self._reset_btn = PushButton(tr("Reset", "重置", "リセット"), query_card)
        self._reset_btn.clicked.connect(self._reset_filters)
        query_row.addWidget(self._reset_btn)
        query_layout.addLayout(query_row)

        self._scope_hint = BodyLabel("", query_card)
        self._scope_hint.setWordWrap(True)
        query_layout.addWidget(self._scope_hint)
        self._query_card = query_card
        root.addWidget(query_card)

        rule_card = CardWidget(self)
        rule_layout = QHBoxLayout(rule_card)
        rule_layout.setContentsMargins(16, 14, 16, 14)
        rule_layout.setSpacing(10)
        rule_layout.addWidget(SubtitleLabel(tr("Download rule", "下载规则", "ダウンロードルール"), rule_card))
        self._rule_picker = RulePicker(rule_card)
        rule_layout.addWidget(self._rule_picker, 1)
        rule_layout.addWidget(
            BodyLabel(
                tr(
                    "The selected rule controls filtering, naming and download behavior.",
                    "所选规则统一控制筛选、命名和下载行为。",
                    "選択したルールがフィルター・命名・保存動作を統一します。",
                ),
                rule_card,
            )
        )
        self._rule_card = rule_card
        root.addWidget(rule_card)

        self._tag_popup = TagSuggestionPopup(self)
        self._tag_popup.suggestion_chosen.connect(self._apply_tag_suggestion)
        self._keyword_edit.editingFinished.connect(self._tag_popup.hide)
        self._keyword_edit.textChanged.connect(
            lambda text: self._show_tag_suggestions(self._keyword_edit, text)
        )
        self._search_history_popup = SearchHistoryPopup(self)
        self._search_history_popup.history_chosen.connect(self._apply_search_history)
        self._search_history_popup.clear_requested.connect(self._clear_search_history)
        self._keyword_edit.activated.connect(self._show_search_history_popup)
        self._keyword_edit.deactivated.connect(self._hide_search_history_popup)
        self._refresh_search_history_popup()

        result_header = QHBoxLayout()
        # Keep paging beside the result controls so it remains readable and is
        # immediately below the download-rule card instead of being stranded
        # in the page's bottom margin.
        pagination = QHBoxLayout()
        pagination.setSpacing(8)
        self._previous_page_btn = ToolButton(self)
        self._previous_page_btn.setIcon(FluentIcon.LEFT_ARROW)
        self._previous_page_btn.setFixedSize(44, 36)
        self._previous_page_btn.setToolTip(
            tr("Previous page", "上一页", "前のページ")
        )
        self._previous_page_btn.clicked.connect(self._go_previous_page)
        pagination.addWidget(self._previous_page_btn)
        self._page_label = BodyLabel("", self)
        self._page_label.setMinimumWidth(112)
        self._page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pagination.addWidget(self._page_label)
        self._jump_page_btn = PushButton(tr("Jump", "跳页", "ページ移動"), self)
        self._jump_page_btn.setToolTip(
            tr("Jump to a page", "输入页码并跳转", "ページ番号を入力して移動")
        )
        self._jump_page_btn.clicked.connect(self._jump_to_page)
        pagination.addWidget(self._jump_page_btn)
        self._next_page_btn = ToolButton(self)
        self._next_page_btn.setIcon(FluentIcon.RIGHT_ARROW)
        self._next_page_btn.setFixedSize(44, 36)
        self._next_page_btn.setToolTip(
            tr("Next page", "下一页", "次のページ")
        )
        self._next_page_btn.clicked.connect(self._go_next_page)
        pagination.addWidget(self._next_page_btn)
        result_header.addLayout(pagination)
        result_header.addSpacing(12)
        self._status_label = BodyLabel(
            tr("Enter a query or search the latest videos", "输入条件后开始搜索，也可以直接查看最新视频", "条件を入力して検索してください"),
            self,
        )
        self._status_label.setWordWrap(True)
        result_header.addWidget(self._status_label, 1)
        result_header.addWidget(BodyLabel(tr("View", "视图", "表示"), self))
        self._view_combo = self._make_combo(
            [
                (tr("Grid", "网格", "グリッド"), "grid"),
                (tr("List", "列表", "リスト"), "list"),
            ],
            self,
        )
        self._view_combo.setFixedWidth(96)
        saved_view = str(app_config.get_ui_value("search_view_mode_v1", "grid") or "grid")
        self._view_combo.setCurrentIndex(1 if saved_view == "list" else 0)
        self._view_combo.currentIndexChanged.connect(self._on_view_changed)
        result_header.addWidget(self._view_combo)
        self._grid_columns_label = BodyLabel(tr("Columns", "每行列数", "1行の列数"), self)
        result_header.addWidget(self._grid_columns_label)
        self._grid_columns_combo = ComboBox(self)
        for columns in range(1, _MAX_GRID_COLUMNS + 1):
            self._add_combo_item(self._grid_columns_combo, str(columns), str(columns))
        try:
            saved_columns = int(app_config.get_ui_value("search_grid_columns_v1", _DEFAULT_GRID_COLUMNS) or _DEFAULT_GRID_COLUMNS)
        except (TypeError, ValueError):
            saved_columns = _DEFAULT_GRID_COLUMNS
        self._grid_columns_combo.setCurrentIndex(max(1, min(_MAX_GRID_COLUMNS, saved_columns)) - 1)
        self._grid_columns_combo.setFixedWidth(84)
        self._grid_columns_combo.currentIndexChanged.connect(self._on_grid_columns_changed)
        result_header.addWidget(self._grid_columns_combo)
        self._result_fields_btn = PushButton(tr("Fields", "字段设置", "字段設定"), self, FluentIcon.SETTING)
        self._result_fields_btn.clicked.connect(self._configure_result_columns)
        result_header.addWidget(self._result_fields_btn)
        self._queue_selected_btn = PrimaryPushButton(
            tr("Add selected", "加入选中项", "選択を追加"), self, FluentIcon.DOWNLOAD
        )
        self._queue_selected_btn.setEnabled(False)
        self._queue_selected_btn.clicked.connect(self._queue_selected)
        result_header.addWidget(self._queue_selected_btn)
        self._open_selected_btn = PushButton(tr("Open selected", "打开选中项", "選択を開く"), self)
        self._open_selected_btn.setEnabled(False)
        self._open_selected_btn.clicked.connect(self._open_selected)
        result_header.addWidget(self._open_selected_btn)
        root.addLayout(result_header)

        result_card = CardWidget(self)
        result_layout = QVBoxLayout(result_card)
        result_layout.setContentsMargins(0, 0, 0, 0)
        self._results = ListWidget(result_card)
        self._results.setFrameShape(QFrame.Shape.NoFrame)
        self._results.setStyleSheet(_search_grid_style())
        self._results.setContentsMargins(0, 0, 0, 0)
        self._results.setViewportMargins(0, 0, 0, 0)
        self._results.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._results.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._results.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._results.setViewMode(ListWidget.ViewMode.IconMode)
        self._results.setResizeMode(ListWidget.ResizeMode.Adjust)
        self._results.setMovement(ListWidget.Movement.Static)
        self._results.setWrapping(True)
        self._results.setWordWrap(True)
        self._results.setUniformItemSizes(True)
        self._results.setIconSize(_VIDEO_ICON_SIZE)
        self._results.setGridSize(QSize(300, _DEFAULT_GRID_HEIGHT))
        self._results.setSpacing(8)
        self._results.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._results.setSelectionRectVisible(True)
        self._results.setTextElideMode(Qt.TextElideMode.ElideRight)
        self._results.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._results.itemDoubleClicked.connect(self._open_item)
        self._results.itemSelectionChanged.connect(self._sync_selection_buttons)
        self._results.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._results.customContextMenuRequested.connect(self._show_context_menu)
        self._results_stack = QStackedWidget(result_card)
        self._results_stack.addWidget(self._results)

        self._results_table = TableWidget(result_card)
        self._results_table.setColumnCount(len(self._RESULT_COLUMN_KEYS))
        self._results_table.setHorizontalHeaderLabels(
            [
                tr("Iwara ID", "Iwara ID", "Iwara ID"),
                tr("Title", "标题", "タイトル"),
                tr("Author", "作者", "作者"),
                tr("Views", "播放", "再生数"),
                tr("Likes", "点赞", "いいね"),
                tr("Comments", "评论", "コメント"),
                tr("Duration", "时长", "長さ"),
                tr("Published", "发布时间", "公開日"),
                tr("Tags", "标签", "タグ"),
                tr("Iwara URL", "Iwara 链接", "Iwara URL"),
                tr("Source URL", "来源链接", "元URL"),
                tr("Source", "来源", "ソース"),
            ]
        )
        self._results_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._results_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._results_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._results_table.setAlternatingRowColors(True)
        self._results_table.setBorderVisible(True)
        self._results_table.setBorderRadius(8)
        self._results_table.setWordWrap(False)
        self._results_table.setSortingEnabled(False)
        self._results_table.verticalHeader().setVisible(False)
        self._results_table.verticalHeader().setDefaultSectionSize(38)
        table_header = self._results_table.horizontalHeader()
        table_header.setHighlightSections(False)
        table_header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        restore_table_widths(
            self._results_table,
            "search_result_widths_v2",
            {
                0: 150,
                1: 420,
                2: 150,
                3: 90,
                4: 90,
                5: 90,
                6: 90,
                7: 120,
                8: 260,
                9: 300,
                10: 300,
                11: 110,
            },
        )
        connect_table_width_saver(self._results_table, "search_result_widths_v2")
        restore_table_columns(
            self._results_table,
            "search_result_table_v2",
            default_visible=[0, 1, 2, 3, 4, 5, 7, 8],
        )
        connect_table_column_saver(self._results_table, "search_result_table_v2")
        self._results_table.itemDoubleClicked.connect(self._open_table_item)
        self._results_table.itemSelectionChanged.connect(self._sync_selection_buttons)
        self._results_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._results_table.customContextMenuRequested.connect(self._show_context_menu)
        self._results_stack.addWidget(self._results_table)
        result_layout.addWidget(self._results_stack)
        root.addWidget(result_card, 1)

        self._on_scope_changed()
        self._on_source_changed()
        self._sync_view_controls()
        self._update_page_controls()
        self._fit_results_table_last_column()
        QTimer.singleShot(0, self._resize_grid)
        saved_collapsed = app_config.get_ui_value("search_controls_collapsed_v1", False)
        if isinstance(saved_collapsed, str):
            saved_collapsed = saved_collapsed.strip().casefold() in {
                "1",
                "true",
                "yes",
                "on",
            }
        self._set_search_controls_collapsed(bool(saved_collapsed), persist=False)

    @staticmethod
    def _add_combo_item(combo: ComboBox, text: str, data: str):
        combo.addItem(text)
        combo.setItemData(combo.count() - 1, data)

    def _toggle_search_controls(self):
        self._set_search_controls_collapsed(not self._search_controls_collapsed)

    def _set_search_controls_collapsed(self, collapsed: bool, *, persist: bool = True):
        self._search_controls_collapsed = bool(collapsed)
        self._query_card.setVisible(not self._search_controls_collapsed)
        self._rule_card.setVisible(not self._search_controls_collapsed)
        if self._search_controls_collapsed:
            self._hide_search_history_popup()
            if self._tag_popup is not None:
                self._tag_popup.hide()
            self._toggle_search_controls_btn.setText(
                tr("Show search controls", "展开搜索区", "検索欄を展開")
            )
            self._toggle_search_controls_btn.setToolTip(
                tr("Show search and download rule controls", "显示搜索与下载规则", "検索・保存ルール欄を表示")
            )
        else:
            self._toggle_search_controls_btn.setText(
                tr("Hide search controls", "收起搜索区", "検索欄を折りたたむ")
            )
            self._toggle_search_controls_btn.setToolTip(
                tr("Hide search and download rule controls", "隐藏搜索与下载规则", "検索・保存ルール欄を隠す")
            )
        if persist:
            app_config.set_ui_value(
                "search_controls_collapsed_v1",
                self._search_controls_collapsed,
            )
        QTimer.singleShot(0, self._resize_grid)

    @staticmethod
    def _make_combo(items: list[tuple[str, str]], parent: QWidget) -> ComboBox:
        combo = ComboBox(parent)
        for text, data in items:
            SearchInterface._add_combo_item(combo, text, data)
        combo.setCurrentIndex(0)
        return combo

    @staticmethod
    def _set_combo_data(combo: ComboBox, value: str) -> bool:
        target = str(value or "").strip()
        for index in range(combo.count()):
            if str(combo.itemData(index) or "") == target:
                combo.setCurrentIndex(index)
                return True
        return False

    @staticmethod
    def _search_history_label(entry: dict[str, str]) -> str:
        keyword = entry.get("keyword", "") or tr(
            "Latest videos", "最新视频", "最新動画"
        )
        scope_labels = {
            "videos": tr("Videos", "视频", "動画"),
            "authors": tr("Authors", "作者", "作者"),
            "tags": tr("Tags", "标签", "タグ"),
            "playlists": tr("Playlists", "播放列表", "プレイリスト"),
        }
        source_labels = {
            "oreno3d": tr("Oreno3D", "Oreno3D", "Oreno3D"),
            "iwara": tr("Iwara", "Iwara", "Iwara"),
        }
        scope = scope_labels.get(entry.get("scope", "videos"), entry.get("scope", "videos"))
        source = source_labels.get(entry.get("source", "oreno3d"), entry.get("source", "oreno3d"))
        return f"{keyword} · {scope} · {source}"

    def _read_search_history(self) -> list[dict[str, str]]:
        history = _decode_search_history(app_config.get_ui_value(_SEARCH_HISTORY_KEY, "[]"))
        return history[: app_config.search_history_limit]

    def _refresh_search_history_popup(self):
        if not hasattr(self, "_search_history_popup"):
            return
        self._search_history_popup.set_history(
            self._read_search_history(),
            self._search_history_label,
        )

    def _show_search_history_popup(self):
        if not hasattr(self, "_search_history_popup"):
            return
        history = self._read_search_history()
        if not history:
            self._search_history_popup.hide()
            return
        self._search_history_popup.resize(
            min(620, max(360, self._keyword_edit.width())),
            min(280, max(60, self._search_history_popup.sizeHint().height())),
        )
        self._search_history_popup.move(
            self._keyword_edit.mapToGlobal(QPoint(0, self._keyword_edit.height()))
        )
        self._search_history_popup.show()
        self._search_history_popup.raise_()

    def _hide_search_history_popup(self):
        if hasattr(self, "_search_history_popup"):
            self._search_history_popup.hide()

    def _record_current_search(self):
        entry = {
            "keyword": self._keyword_edit.text().strip(),
            "source": str(self._source_combo.currentData() or "oreno3d"),
            "scope": str(self._scope_combo.currentData() or "videos"),
            "sort": str(self._sort_combo.currentData() or "date"),
        }
        history = _upsert_search_history(
            _decode_search_history(app_config.get_ui_value(_SEARCH_HISTORY_KEY, "[]")),
            entry,
            app_config.search_history_limit,
        )
        app_config.set_ui_value(
            _SEARCH_HISTORY_KEY,
            json.dumps(history, ensure_ascii=False, separators=(",", ":")),
        )
        self._refresh_search_history_popup()

    def _apply_search_history(self, payload: str):
        decoded = _decode_search_history(payload)
        if not decoded:
            return
        entry = decoded[0]
        self._auto_search_timer.stop()
        self._set_combo_data(self._source_combo, entry["source"])
        self._set_combo_data(self._scope_combo, entry["scope"])
        self._set_combo_data(self._sort_combo, entry["sort"])
        self._keyword_edit.setText(entry["keyword"])
        self._auto_search_timer.stop()
        QTimer.singleShot(0, self._start_search)

    def _clear_search_history(self):
        app_config.set_ui_value(_SEARCH_HISTORY_KEY, "[]")
        self._refresh_search_history_popup()

    @staticmethod
    def _search_resolution_mode() -> str:
        mode = str(
            app_config.get_ui_value("search_iwara_resolution_mode_v1", "eager")
            or "eager"
        ).strip().lower()
        return mode if mode in {"eager", "on_demand"} else "eager"

    @staticmethod
    def _search_resolution_concurrency() -> int:
        try:
            value = int(
                app_config.get_ui_value(
                    "search_iwara_resolution_workers_v1",
                    _DEFAULT_SEARCH_RESOLUTION_CONCURRENCY,
                )
                or _DEFAULT_SEARCH_RESOLUTION_CONCURRENCY
            )
        except (TypeError, ValueError):
            value = _DEFAULT_SEARCH_RESOLUTION_CONCURRENCY
        return max(1, min(_MAX_SEARCH_RESOLUTION_CONCURRENCY, value))

    @staticmethod
    def _cover_download_concurrency() -> int:
        try:
            value = int(
                app_config.get_ui_value(
                    "cover_download_workers_v1",
                    _DEFAULT_COVER_DOWNLOAD_CONCURRENCY,
                )
                or _DEFAULT_COVER_DOWNLOAD_CONCURRENCY
            )
        except (TypeError, ValueError):
            value = _DEFAULT_COVER_DOWNLOAD_CONCURRENCY
        return max(1, min(_MAX_COVER_DOWNLOAD_CONCURRENCY, value))

    def _is_list_view(self) -> bool:
        return (
            str(self._view_combo.currentData() or "grid") == "list"
            and str(self._scope_combo.currentData() or "videos") != "authors"
        )

    def _sync_view_controls(self):
        if not hasattr(self, "_view_combo"):
            return
        if str(self._scope_combo.currentData() or "videos") == "authors":
            self._view_combo.blockSignals(True)
            self._view_combo.setCurrentIndex(0)
            self._view_combo.blockSignals(False)
        list_mode = self._is_list_view()
        grid_mode = not list_mode
        self._grid_columns_label.setVisible(grid_mode)
        self._grid_columns_combo.setVisible(grid_mode)
        self._result_fields_btn.setVisible(list_mode)
        if hasattr(self, "_results_stack"):
            self._results_stack.setCurrentIndex(1 if list_mode else 0)

    def _on_view_changed(self, _index: int):
        app_config.set_ui_value(
            "search_view_mode_v1",
            str(self._view_combo.currentData() or "grid"),
        )
        self._sync_view_controls()
        self._render_results()
        if not self._is_list_view():
            self._start_image_loading()

    def _on_grid_columns_changed(self, _index: int):
        try:
            value = int(self._grid_columns_combo.currentData() or _DEFAULT_GRID_COLUMNS)
        except (TypeError, ValueError):
            value = _DEFAULT_GRID_COLUMNS
        value = max(1, min(_MAX_GRID_COLUMNS, value))
        app_config.set_ui_value("search_grid_columns_v1", value)
        self._resize_grid()
        self._schedule_grid_resize()

    def _configure_result_columns(self):
        open_table_column_dialog(
            self._results_table,
            "search_result_table_v2",
            title=tr("Search Result Fields", "搜索结果字段", "検索結果の列設定"),
            default_visible=[0, 1, 2, 3, 4, 5, 7, 8],
            parent=self,
        )
        self._fit_results_table_last_column()

    def _fit_results_table_last_column(self):
        if not hasattr(self, "_results_table"):
            return
        header = self._results_table.horizontalHeader()
        visible_columns = [
            column
            for column in range(self._results_table.columnCount())
            if not self._results_table.isColumnHidden(column)
        ]
        if not visible_columns:
            return
        visible_columns.sort(key=header.visualIndex)
        last_column = visible_columns[-1]
        for column in visible_columns:
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(last_column, QHeaderView.ResizeMode.Stretch)

    def _on_scope_changed(self, *_args, trigger_search: bool = True):
        scope = str(self._scope_combo.currentData() or "videos")
        if scope == "authors":
            hint = tr(
                "Author mode uses the Iwara live API. Switch the source if needed.",
                "作者模式使用 Iwara 实时 API；如需切换请修改数据源。",
                "作者モードはIwaraライブAPIを使用します。必要ならソースを切り替えてください。",
            )
            self._keyword_edit.setPlaceholderText(
                tr("Author username…", "输入作者用户名…", "作者ユーザー名…")
            )
        elif scope == "tags":
            if str(self._source_combo.currentData() or "oreno3d") == "oreno3d":
                hint = tr(
                    "Mapped Iwara labels use typed numeric Oreno3D routes; unknown names fall back to keyword search. Multiple tags are matched by intersection. tag:<id>, origin:<id>, and character:<id> are also supported.",
                    "已匹配的 Iwara 标签会自动转为对应的 Oreno3D 数字路由；未知名称回退到关键词搜索。多个标签会取交集；也支持 tag:<id>、origin:<id>、character:<id>。",
                    "対応するIwaraラベルはOreno3Dの型付き数値ルートに変換し、未知名はキーワード検索に戻します。複数タグは共通結果を求めます。tag:<id>・origin:<id>・character:<id>にも対応します。",
                )
            else:
                hint = tr(
                    "Type English, Chinese, or Japanese tags for the Iwara API.",
                    "输入英文、中文或日文标签，并提交给 Iwara API。",
                    "英語・中国語・日本語のタグをIwara APIに送信します。",
                )
            self._keyword_edit.setPlaceholderText(
                tr(
                    "Type a tag, select a candidate, then add another…",
                    "输入标签，选择候选后可继续添加…",
                    "タグを入力し、候補選択後に続けて追加…",
                )
            )
        elif scope == "playlists":
            hint = tr(
                "Paste a playlist ID or an iwara.tv/playlist/... URL.",
                "可输入播放列表 ID，或粘贴 iwara.tv/playlist/... 链接。",
                "プレイリストIDまたはiwara.tv/playlist/... URLを入力できます。",
            )
            self._keyword_edit.setPlaceholderText(
                tr("Playlist ID or URL…", "输入播放列表 ID 或链接…", "プレイリストIDまたはURL…")
            )
        else:
            hint = tr(
                "Oreno3D is only a search bridge. Opening a result resolves and shows the final Iwara page.",
                "Oreno3D 仅作为搜索桥接；打开结果时会解析并展示最终的 Iwara 页面。",
                "Oreno3Dは検索ブリッジのみです。結果を開くとIwaraのページを表示します。",
            )
            self._keyword_edit.setPlaceholderText(
                tr(
                    "Keywords or titles…",
                    "输入关键词或标题…",
                    "キーワードまたはタイトル…",
                )
            )
        self._scope_hint.setText(hint)
        if scope != "tags" and self._tag_popup is not None:
            self._tag_popup.hide()
        self._sync_view_controls()
        if trigger_search:
            self._schedule_auto_search()

    def _scope_index(self, scope: str) -> int:
        for index in range(self._scope_combo.count()):
            if str(self._scope_combo.itemData(index) or "") == scope:
                return index
        return -1

    def _sync_scope_options_for_source(self, source: str):
        """Expose only scopes supported by the selected online source."""

        supported = {"videos", "tags"} if source == "oreno3d" else {
            "videos",
            "authors",
            "tags",
            "playlists",
        }
        current_scope = str(self._scope_combo.currentData() or "videos")
        self._scope_combo.blockSignals(True)
        self._scope_combo.clear()
        for text, data in self._scope_items:
            if data in supported:
                self._add_combo_item(self._scope_combo, text, data)
        selected_scope = current_scope if current_scope in supported else "videos"
        selected_index = self._scope_index(selected_scope)
        if selected_index >= 0:
            self._scope_combo.setCurrentIndex(selected_index)
        self._scope_combo.blockSignals(False)

    def _on_source_changed(self, *_args, trigger_search: bool = True):
        source = str(self._source_combo.currentData() or "oreno3d")
        self._sync_scope_options_for_source(source)
        if source == "oreno3d":
            self._source_status_label.setText(
                tr(
                    "Oreno3D bridge → Iwara · videos/tags · images cached in data/img/search",
                    "Oreno3D 桥接 → Iwara · 支持视频/标签 · 图片缓存于 data/img/search",
                    "Oreno3Dブリッジ → Iwara・動画/タグ・画像は data/img/search にキャッシュ",
                )
            )
        else:
            self._source_status_label.setText(
                tr(
                    "Live API · videos/authors/tags/playlists · images cached in data/img/search",
                    "实时 API · 支持视频/作者/标签/播放列表 · 图片缓存于 data/img/search",
                    "ライブAPI・動画/作者/タグ/プレイリスト・画像は data/img/search にキャッシュ",
                )
            )
        self._on_scope_changed(trigger_search=False)
        if trigger_search:
            self._schedule_auto_search()

    def _on_sort_changed(self, *_args):
        self._schedule_auto_search()

    def _schedule_auto_search(self):
        # Drop a timer scheduled under the previous source/scope state.  A
        # stale timer can otherwise start a new generation during paging and
        # make the requested page result get discarded as obsolete.
        self._auto_search_timer.stop()
        if not self._auto_search_ready or not app_config.search_auto_search_enabled:
            return
        scope = str(self._scope_combo.currentData() or "videos")
        if scope in {"authors", "tags", "playlists"} and not self._keyword_edit.text().strip():
            return
        self._auto_search_timer.start()

    def _auto_start_search(self):
        if self._auto_search_ready:
            self._start_search()

    def _show_tag_suggestions(self, edit: LineEdit, text: str):
        if self._tag_popup is None:
            return
        if str(text or "").strip():
            self._hide_search_history_popup()
        if str(self._scope_combo.currentData() or "videos") != "tags":
            self._tag_popup.hide()
            return
        query = tag_suggestion_query(text)
        if not query:
            self._tag_popup.hide()
            return
        suggestions = download_manager.get_search_tag_suggestions(query, limit=12)
        if not suggestions:
            self._tag_popup.hide()
            return
        self._active_tag_edit = edit
        self._tag_popup.set_suggestions(suggestions)
        self._tag_popup.resize(
            min(560, max(360, edit.width())),
            min(260, max(60, self._tag_popup.sizeHint().height())),
        )
        self._tag_popup.move(edit.mapToGlobal(QPoint(0, edit.height())))
        self._tag_popup.show()
        self._tag_popup.raise_()

    def _apply_tag_suggestion(self, key: str):
        edit = self._active_tag_edit
        if edit is None:
            return
        if self._tag_popup is not None:
            self._tag_popup.hide()
        edit.setText(apply_tag_suggestion(edit.text(), key))
        edit.setFocus(Qt.FocusReason.OtherFocusReason)
        edit.setCursorPosition(len(edit.text()))
        QTimer.singleShot(0, lambda edit=edit: self._restore_tag_edit_focus(edit))

    def _restore_tag_edit_focus(self, edit: LineEdit):
        if edit is not self._active_tag_edit or not edit.isVisible() or not edit.isEnabled():
            return
        edit.setFocus(Qt.FocusReason.OtherFocusReason)
        edit.setCursorPosition(len(edit.text()))

    def _build_filters(self) -> SearchFilters:
        return SearchFilters(
            keyword=self._keyword_edit.text().strip(),
            sort=str(self._sort_combo.currentData() or "date"),
            page_size=36 if str(self._source_combo.currentData() or "oreno3d") == "oreno3d" else 32,
        )

    def _start_search(self, *_args):
        self._auto_search_timer.stop()
        self._hide_search_history_popup()
        try:
            filters = self._build_filters()
        except ValueError as exc:
            self._show_error(str(exc))
            return
        scope = str(self._scope_combo.currentData() or "videos")
        source = str(self._source_combo.currentData() or "oreno3d")
        if scope in {"authors", "playlists"} and source == "oreno3d":
            self._show_warning(
                tr(
                    "Oreno3D online search currently returns video cards. Switch to Iwara live API for author or playlist results.",
                    "Oreno3D 在线搜索当前返回视频卡片；作者或播放列表结果请切换到 Iwara 实时 API。",
                    "Oreno3Dオンライン検索は現在動画カードを返します。作者・プレイリストはIwaraライブAPIへ切り替えてください。",
                )
            )
            return
        if scope == "authors" and not filters.keyword:
            self._show_error(tr("Enter an author username first", "请先输入作者用户名", "作者ユーザー名を入力してください"))
            return
        if scope == "tags" and not filters.keyword:
            self._show_error(tr("Enter at least one tag", "请至少输入一个标签", "タグを1つ以上入力してください"))
            return
        if scope == "playlists" and not filters.keyword:
            self._show_error(tr("Enter a playlist ID or URL", "请输入播放列表 ID 或链接", "プレイリストIDまたはURLを入力してください"))
            return

        self._record_current_search()
        self._interrupt_search_workers()
        self._current_page = 0
        self._last_page = None
        self._pending_open_video_ids.clear()
        self._pending_open_author_video_ids.clear()
        self._next_page = 0
        self._total = None
        self._all_videos.clear()
        self._all_authors.clear()
        self._image_path_by_key.clear()
        self._image_pending_keys.clear()
        self._render_results()
        self._run_search(filters, scope, source=source, page=0, replace_results=True)

    def _interrupt_search_workers(self):
        self._generation += 1
        self._pending_author_subscription_video_ids.clear()
        self._pending_open_author_video_ids.clear()
        for worker in self._search_workers:
            worker.requestInterruption()
        for worker in self._image_workers:
            worker.requestInterruption()
        for worker in self._oreno_link_workers:
            worker.requestInterruption()
        for worker in self._iwara_author_workers:
            worker.requestInterruption()
        for worker in self._oreno_author_workers:
            worker.requestInterruption()

    def shutdown(self, *, timeout_ms: int = 30_000) -> bool:
        """Stop all page-owned search and image workers before window teardown."""
        self._interrupt_search_workers()
        workers: list[QThread | None] = [
            *self._search_workers,
            *self._image_workers,
            *self._oreno_link_workers,
            *self._iwara_author_workers,
            *self._oreno_author_workers,
            self._queue_resolve_worker,
        ]
        return stop_qthreads(workers, timeout_ms=timeout_ms)

    def _load_more(self):
        """Compatibility alias for callers that used the old load-more action."""

        self._go_next_page()

    def _go_previous_page(self):
        if self._current_page <= 0:
            return
        self._navigate_to_page(self._current_page - 1)

    def _go_next_page(self):
        if self._last_page is not None:
            if self._current_page >= self._last_page:
                return
            page = self._current_page + 1
        elif self._next_page is not None:
            page = self._next_page
        else:
            return
        self._navigate_to_page(page)

    def _jump_to_page(self):
        if self._last_page is None and self._total is None and not self._all_videos and not self._all_authors:
            self._show_warning(
                tr(
                    "Run a search before jumping to a page.",
                    "请先执行搜索，再跳转页码。",
                    "ページ移動の前に検索を実行してください。",
                )
            )
            return

        current = max(1, self._current_page + 1)
        page_text, accepted = show_fluent_text_input(
            self,
            tr("Jump to page", "跳转页码", "ページへ移動"),
            tr("Page number:", "页码：", "ページ番号:"),
            text=str(current),
            accept_text=tr("Go", "跳转", "移動"),
            cancel_text=tr("Cancel", "取消", "キャンセル"),
        )
        if not accepted:
            return
        try:
            page_number = int(page_text.strip())
        except (TypeError, ValueError):
            self._show_error(
                tr("Enter a valid page number.", "请输入有效的页码。", "有効なページ番号を入力してください。")
            )
            return
        if page_number < 1:
            self._show_error(
                tr("Page number must be at least 1.", "页码必须大于等于 1。", "ページ番号は1以上にしてください。")
            )
            return
        if self._last_page is not None and page_number > self._last_page + 1:
            self._show_error(
                tr(
                    f"Page number must be between 1 and {self._last_page + 1}.",
                    f"页码必须在 1 到 {self._last_page + 1} 之间。",
                    f"ページ番号は1～{self._last_page + 1}の範囲で指定してください。",
                )
            )
            return
        self._navigate_to_page(page_number - 1)

    def _navigate_to_page(self, page: int):
        self._auto_search_timer.stop()
        page = max(0, int(page))
        if self._last_page is not None:
            page = min(page, self._last_page)
        try:
            filters = self._build_filters()
        except ValueError as exc:
            self._show_error(str(exc))
            return
        scope = str(self._scope_combo.currentData() or "videos")
        source = str(self._source_combo.currentData() or "oreno3d")
        self._interrupt_search_workers()
        self._pending_open_video_ids.clear()
        self._pending_open_author_video_ids.clear()
        self._current_page = page
        self._next_page = None
        self._run_search(filters, scope, source=source, page=page, replace_results=True)

    def _run_search(self, filters: SearchFilters, scope: str, *, source: str, page: int, replace_results: bool):
        worker = SearchWorker(
            filters,
            scope if scope in {"videos", "authors", "tags", "playlists"} else "videos",
            page,
            self._generation,
            replace_results=replace_results,
            source=source,
        )
        self._search_workers.append(worker)
        worker.result_ready.connect(self._on_search_result)
        worker.finished.connect(lambda worker=worker: self._cleanup_search_worker(worker))
        self._set_loading(True)
        worker.start()

    def _on_search_result(self, result: SearchPageResult):
        worker = self.sender()
        if not isinstance(worker, SearchWorker) or worker.generation != self._generation:
            return
        if worker.replace_results:
            self._all_videos.clear()
            self._all_authors.clear()
        existing_video_ids = {video.video_id for video in self._all_videos}
        for video in result.videos:
            if video.video_id not in existing_video_ids:
                self._all_videos.append(video)
                existing_video_ids.add(video.video_id)
        existing_author_ids = {author.author_id for author in self._all_authors}
        for author in result.authors:
            if author.author_id not in existing_author_ids:
                self._all_authors.append(author)
                existing_author_ids.add(author.author_id)
        if result.total is not None:
            self._total = result.total
        self._current_page = max(0, int(result.current_page or 0))
        self._last_page = result.last_page
        self._next_page = result.next_page
        self._render_results()
        # The first result can arrive before the parent window has completed
        # its final layout.  Recalculate once on the next event-loop turn so
        # the first paint uses the selected column count, not a stale width.
        QTimer.singleShot(0, self._resize_grid)
        self._set_loading(False)
        if result.error:
            self._show_warning(result.error)
        self._start_image_loading()
        if self._search_resolution_mode() == "eager":
            self._start_oreno_link_resolution(result.videos, hydrate_metadata=True)
        self._update_status(result)

    def _cleanup_search_worker(self, worker: SearchWorker):
        if worker in self._search_workers:
            self._search_workers.remove(worker)
        worker.deleteLater()
        if not any(item.isRunning() and item.generation == self._generation for item in self._search_workers):
            self._set_loading(False)

    def _start_oreno_link_resolution(
        self,
        videos: list[SearchVideo],
        *,
        priority: bool = False,
        hydrate_metadata: bool = True,
    ):
        pending = {
            video.video_id
            for worker in self._oreno_link_workers
            if worker.isRunning() and worker.generation == self._generation
            for video in worker.videos
        }
        candidates = [
            video
            for video in videos
            if video.source_kind == "oreno3d"
            and not video.iwara_url
            and not video.download_video_id
            and (priority or video.video_id not in pending)
        ]
        if not candidates:
            return
        worker = SearchOrenoLinkWorker(
            self._generation,
            candidates,
            concurrency=1 if priority else self._search_resolution_concurrency(),
            hydrate_metadata=hydrate_metadata,
        )
        self._oreno_link_workers.append(worker)
        worker.item_ready.connect(self._on_oreno_link_item)
        worker.result_ready.connect(self._on_oreno_links_resolved)
        worker.progress.connect(self._on_oreno_link_progress)
        worker.finished.connect(lambda worker=worker: self._cleanup_oreno_link_worker(worker))
        worker.start()

    def _on_oreno_link_progress(self, current: int, total: int):
        if total <= 0:
            return
        self._status_label.setText(
            tr(
                f"Loading Iwara IDs {current}/{total}…",
                f"正在加载 Iwara ID {current}/{total}…",
                f"Iwara IDを読み込み中 {current}/{total}…",
            )
        )

    def _apply_oreno_link(self, video: SearchVideo, link: dict[str, Any]) -> bool:
        video_id = str(link.get("id") or "").strip()
        if not video_id:
            return False
        iwara_url = str(link.get("url") or "").strip()
        # Oreno3D's cover is already loaded asynchronously when the search
        # card appears.  Keep that bridge cover after Iwara metadata arrives;
        # otherwise the metadata's thumbnail URL invalidates the cache key and
        # the same card visibly downloads a second image from Iwara.
        is_oreno_bridge = video.source_kind == "oreno3d" or bool(video.raw.get("oreno3d_url"))
        bridge_thumbnail_url = str(
            video.raw.get("oreno3d_thumbnail_url") or video.thumbnail_url or ""
        )
        if is_oreno_bridge and "oreno3d_thumbnail_url" not in video.raw:
            video.raw["oreno3d_thumbnail_url"] = bridge_thumbnail_url
        bridge_url = str(video.raw.get("oreno3d_url") or video.source_url or "")
        metadata = link.get("metadata")
        normalized = normalize_video(metadata) if isinstance(metadata, dict) else None
        if normalized is not None:
            for field_name in (
                "title",
                "author_username",
                "author_name",
                "published_at",
                "likes",
                "views",
                "duration",
                "comments",
                "rating",
                "tags",
                "origins",
                "characters",
                "thumbnail_url",
                "slug",
            ):
                setattr(video, field_name, getattr(normalized, field_name))
        if is_oreno_bridge:
            video.thumbnail_url = bridge_thumbnail_url
        if isinstance(metadata, dict):
            video.raw.update(metadata)
            video.raw["_iwara_metadata_loaded"] = normalized is not None
        video.raw["oreno3d_url"] = bridge_url
        video.raw["oreno3d_id"] = video.video_id.removeprefix("oreno3d:")
        video.download_video_id = video_id
        video.iwara_url = iwara_url or f"https://www.iwara.tv/video/{video_id}"
        video.downloadable = True
        video.source_kind = "iwara"
        video.source_url = video.iwara_url
        video.raw["iwara_id"] = video_id
        video.raw["iwara_url"] = video.iwara_url
        return True

    def _on_oreno_link_item(self, result: object):
        if not isinstance(result, dict) or int(result.get("generation", -1)) != self._generation:
            return
        video_key = str(result.get("video_id") or "")
        link = result.get("link")
        if not video_key or not isinstance(link, dict):
            return
        video = next(
            (candidate for candidate in self._all_videos if candidate.video_id == video_key),
            None,
        )
        if video is None or not self._apply_oreno_link(video, link):
            return

        focused = self.focusWidget()
        if video.video_id in self._pending_open_video_ids:
            self._pending_open_video_ids.discard(video.video_id)
            # The ID stage is emitted before metadata hydration.  Open the
            # canonical Iwara page now; rendering and metadata can follow.
            webbrowser.open(video.iwara_url)
        self._update_video_presentation(video)
        self._start_image_loading()
        self._maybe_subscribe_pending_author(video)
        if isinstance(focused, LineEdit) and focused.isVisible():
            QTimer.singleShot(0, lambda focused=focused: self._restore_tag_edit_focus(focused))
        self._update_status()

    def _on_oreno_links_resolved(self, result: object):
        if not isinstance(result, dict) or int(result.get("generation", -1)) != self._generation:
            return
        links = result.get("links") or {}
        errors = [str(error) for error in result.get("errors") or []]
        pending_ids = list(self._pending_author_subscription_video_ids)
        for video_key in pending_ids:
            video = next(
                (candidate for candidate in self._all_videos if candidate.video_id == video_key),
                None,
            )
            if video is None:
                self._pending_author_subscription_video_ids.discard(video_key)
                continue
            self._maybe_subscribe_pending_author(video)
            if video_key in self._pending_author_subscription_video_ids and (
                video_key not in links or errors
            ):
                self._pending_author_subscription_video_ids.discard(video_key)
                self._show_warning(
                    tr(
                        "Could not resolve the Iwara author for this Oreno3D result",
                        "无法解析该 Oreno3D 结果对应的 Iwara 作者",
                        "この Oreno3D 結果に対応する Iwara 作者を解析できません",
                    )
                )
        self._update_status()
        if errors and not links:
            self._show_warning(errors[0])

    def _maybe_subscribe_pending_author(self, video: SearchVideo):
        if video.video_id not in self._pending_author_subscription_video_ids:
            return
        target = _author_subscription_target(video)
        if not target:
            return
        self._pending_author_subscription_video_ids.discard(video.video_id)
        self._subscribe_to_author(target)

    def _cleanup_oreno_link_worker(self, worker: SearchOrenoLinkWorker):
        if worker in self._oreno_link_workers:
            self._oreno_link_workers.remove(worker)
        worker.deleteLater()

    def _start_image_loading(self):
        if self._is_list_view() and not self._all_authors:
            return
        jobs: list[tuple[str, str, str]] = []
        pending_keys = set(self._image_pending_keys)
        for worker in self._image_workers:
            if worker.isRunning():
                pending_keys.update(
                    f"{kind}:{item_key}" for kind, item_key, _image_url in worker.jobs
                )
        if self._all_videos:
            for video in self._all_videos:
                if video.thumbnail_url:
                    key = f"video:{video.video_id}"
                    if key not in self._image_path_by_key and key not in pending_keys:
                        jobs.append(("video", video.video_id, video.thumbnail_url))
        if self._all_authors:
            for author in self._all_authors:
                if author.avatar_url:
                    key = f"author:{author.author_id}"
                    if key not in self._image_path_by_key and key not in pending_keys:
                        jobs.append(("author", author.author_id, author.avatar_url))
        if not jobs:
            return
        self._image_pending_keys.update(f"{kind}:{item_key}" for kind, item_key, _ in jobs)
        worker = SearchImageWorker(
            self._generation,
            jobs,
            concurrency=self._cover_download_concurrency(),
        )
        self._image_workers.append(worker)
        worker.image_ready.connect(self._on_image_ready)
        worker.finished.connect(lambda worker=worker: self._cleanup_image_worker(worker))
        worker.start()

    def _on_image_ready(self, generation: int, kind: str, item_key: str, path: str):
        if generation != self._generation:
            return
        key = f"{kind}:{item_key}"
        if not key:
            return
        self._image_path_by_key[key] = path
        self._image_pending_keys.discard(key)
        item = self._item_by_key.get(key)
        if item:
            item.setIcon(self._image_icon(path))

    def _cleanup_image_worker(self, worker: SearchImageWorker):
        if worker in self._image_workers:
            self._image_workers.remove(worker)
        for kind, item_key, _image_url in worker.jobs:
            key = f"{kind}:{item_key}"
            if key not in self._image_path_by_key:
                self._image_pending_keys.discard(key)
        worker.deleteLater()

    def _render_results(self):
        self._results.setUpdatesEnabled(False)
        try:
            self._results.clear()
            self._results_table.clearContents()
            self._results_table.setRowCount(0)
            self._item_by_key.clear()
            scope = str(self._scope_combo.currentData() or "videos")
            list_mode = self._is_list_view() and scope != "authors"
            if scope == "authors":
                for author in self._all_authors:
                    self._add_author_item(author)
            elif list_mode:
                self._render_video_table()
            else:
                for video in self._all_videos:
                    self._add_video_item(video)
            self._sync_view_controls()
            self._resize_grid()
            self._fit_results_table_last_column()
            self._sync_selection_buttons()
        finally:
            self._results.setUpdatesEnabled(True)
            self._results.doItemsLayout()
            self._results.viewport().update()
        self._schedule_grid_resize()

    def _render_video_table(self):
        self._results_table.setUpdatesEnabled(False)
        try:
            self._results_table.setRowCount(len(self._all_videos))
            for row, video in enumerate(self._all_videos):
                values = [
                    self._result_field_value(video, key)
                    for key in self._RESULT_COLUMN_KEYS
                ]
                data = {
                    "kind": "video",
                    "key": f"video:{video.video_id}",
                    "data": video,
                }
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setData(self._DATA_ROLE, data)
                    item.setToolTip(value or "—")
                    if column in {3, 4, 5}:
                        item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                    self._results_table.setItem(row, column, item)
        finally:
            self._results_table.setUpdatesEnabled(True)

    def _update_video_presentation(self, video: SearchVideo):
        """Update one hydrated result without rebuilding the whole page."""

        key = f"video:{video.video_id}"
        data = {"kind": "video", "key": key, "data": video}
        if self._is_list_view():
            for row in range(self._results_table.rowCount()):
                first_item = self._results_table.item(row, 0)
                first_data = first_item.data(self._DATA_ROLE) if first_item else None
                if not isinstance(first_data, dict) or first_data.get("key") != key:
                    continue
                values = [self._result_field_value(video, field) for field in self._RESULT_COLUMN_KEYS]
                for column, value in enumerate(values):
                    item = self._results_table.item(row, column)
                    if item is None:
                        item = QTableWidgetItem()
                        self._results_table.setItem(row, column, item)
                    item.setText(value)
                    item.setData(self._DATA_ROLE, data)
                    item.setToolTip(value or "—")
                self._sync_selection_buttons()
                return

        item = self._item_by_key.get(key)
        if item is None:
            return
        item.setData(self._DATA_ROLE, data)
        item.setText(self._video_card_text(video))
        item.setToolTip(
            f"{video.title}\n"
            f"Iwara: {video.iwara_url or 'resolving…'}\n"
            f"Source: {video.raw.get('oreno3d_url') or video.source_url}"
        )
        item.setSizeHint(getattr(self, "_grid_item_size", QSize(300, _DEFAULT_GRID_HEIGHT)))
        if key in self._image_path_by_key:
            item.setIcon(self._image_icon(self._image_path_by_key[key]))
        self._resize_grid()
        self._sync_selection_buttons()

    def _result_field_value(self, video: SearchVideo, key: str) -> str:
        if key == "iwara_id":
            if video.download_video_id:
                return video.download_video_id
            return "…" if video.source_kind == "oreno3d" else "—"
        if key == "title":
            return video.title or video.video_id
        if key == "author":
            return video.author_name or video.author_username or "—"
        if key == "views":
            return _format_count(video.views)
        if key == "likes":
            return _format_count(video.likes)
        if key == "comments":
            if video.comments or video.source_kind != "oreno3d":
                return _format_count(video.comments)
            return "—"
        if key == "duration":
            return _format_duration(video.duration) if video.duration else "—"
        if key == "published":
            return video.published_at[:19] if video.published_at else "—"
        if key == "tags":
            return ", ".join(video.tags) or "—"
        if key == "iwara_url":
            if video.iwara_url:
                return video.iwara_url
            if video.download_video_id:
                return f"https://www.iwara.tv/video/{video.download_video_id}"
            return "解析中…" if video.source_kind == "oreno3d" else "—"
        if key == "source_url":
            return video.source_url or "—"
        if key == "source":
            return tr("Oreno3D", "Oreno3D", "Oreno3D") if video.source_kind == "oreno3d" else "Iwara"
        return "—"

    def _video_card_text(self, video: SearchVideo) -> str:
        date_text = video.published_at[:10] if video.published_at else "—"
        author = f"@{video.author_username}" if video.author_username else tr("Unknown author", "未知作者", "作者不明")
        stats = tr(
            f"{_format_count(video.views)} views · {_format_count(video.likes)} likes",
            f"{_format_count(video.views)} 次观看 · {_format_count(video.likes)} 喜欢",
            f"{_format_count(video.views)} 再生 · {_format_count(video.likes)} いいね",
        )
        duration = _format_duration(video.duration) if video.duration else "—"
        return (
            f"{_short_text(video.title, 46)}\n"
            f"{author}\n"
            f"{stats}\n"
            f"{duration}\n"
            f"{date_text}"
        )

    def _add_video_item(self, video: SearchVideo):
        key = f"video:{video.video_id}"
        item = QListWidgetItem(self._placeholder_icon("video"), self._video_card_text(video))
        item.setData(self._DATA_ROLE, {"kind": "video", "key": key, "data": video})
        item.setForeground(QColor("#f7fbff" if isDarkTheme() else "#17343b"))
        item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        item.setToolTip(
            f"{video.title}\n"
            f"Iwara: {video.iwara_url or 'resolving…'}\n"
            f"Source: {video.source_url}"
        )
        item.setSizeHint(getattr(self, "_grid_item_size", QSize(300, _DEFAULT_GRID_HEIGHT)))
        if key in self._image_path_by_key:
            item.setIcon(self._image_icon(self._image_path_by_key[key]))
        self._results.addItem(item)
        self._item_by_key[key] = item

    def _add_author_item(self, author: SearchAuthor):
        key = f"author:{author.author_id}"
        count = tr(
            f"{_format_count(author.video_count)} videos",
            f"{_format_count(author.video_count)} 个视频",
            f"動画 {_format_count(author.video_count)} 件",
        )
        text = f"{_short_text(author.name or author.username, 42)}\n@{author.username}\n{count}"
        if author.bio:
            text += f"\n{_short_text(author.bio, 64)}"
        item = QListWidgetItem(self._placeholder_icon("author"), text)
        item.setData(self._DATA_ROLE, {"kind": "author", "key": key, "data": author})
        item.setForeground(QColor("#f7fbff" if isDarkTheme() else "#17343b"))
        item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        item.setToolTip(f"{author.username}\n{author.source_url}")
        item.setSizeHint(QSize(300, _DEFAULT_GRID_HEIGHT))
        if key in self._image_path_by_key:
            item.setIcon(self._image_icon(self._image_path_by_key[key]))
        self._results.addItem(item)
        self._item_by_key[key] = item

    def _placeholder_icon(self, kind: str) -> QIcon:
        pixmap = QPixmap(getattr(self, "_grid_icon_size", _VIDEO_ICON_SIZE))
        pixmap.fill(QColor("#30343b" if isDarkTheme() else "#edf0f5"))
        return QIcon(pixmap)

    def _image_icon(self, path: str) -> QIcon:
        pixmap = QPixmap(path)
        if pixmap.isNull():
            return QIcon()
        size = getattr(self, "_grid_icon_size", _VIDEO_ICON_SIZE)
        scaled = pixmap.scaled(
            size,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        if scaled.width() > size.width() or scaled.height() > size.height():
            x = max(0, (scaled.width() - size.width()) // 2)
            y = max(0, (scaled.height() - size.height()) // 2)
            scaled = scaled.copy(x, y, size.width(), size.height())
        return QIcon(scaled)

    def _resize_grid(self):
        if not hasattr(self, "_results"):
            return
        width = self._results.viewport().width()
        if width <= 0:
            return
        try:
            requested_columns = int(
                self._grid_columns_combo.currentData() or _DEFAULT_GRID_COLUMNS
            )
        except (TypeError, ValueError):
            requested_columns = _DEFAULT_GRID_COLUMNS
        spacing = 12
        # Keep a small fixed reserve for the vertical scrollbar and Qt's list
        # layout rounding.  Without it a 1208px viewport calculates 301px
        # cells, which makes IconMode wrap four requested columns into three as
        # soon as the scrollbar becomes visible.
        scrollbar_reserve = max(
            8,
            int(self._results.verticalScrollBar().sizeHint().width()) - 1,
        )
        available_width = max(1, width - scrollbar_reserve)
        # The selector is an explicit user preference.  Keep that exact
        # column count even on compact panes and calculate a smaller cell
        # instead of silently reducing 8 columns to 4.
        columns = max(1, min(_MAX_GRID_COLUMNS, requested_columns))
        cell_width = max(
            40,
            (available_width - spacing * (columns - 1)) // columns,
        )
        # Let the cover occupy the card width.  The old 260px cap made a
        # one-column result look like a small preview floating in a large
        # blank card, and was especially obvious on wide displays.
        image_width = max(40, cell_width - 12)
        image_height = max(40, round(image_width * 9 / 16))
        text_height = _grid_text_height(
            self._results,
            max(40, cell_width - 12),
            fallback_lines=5,
        )
        grid_height = image_height + text_height + 14
        self._grid_icon_size = QSize(image_width, image_height)
        self._grid_item_size = QSize(cell_width, grid_height)
        updates_enabled = self._results.updatesEnabled()
        self._results.setUpdatesEnabled(False)
        try:
            self._results.setIconSize(self._grid_icon_size)
            self._results.setGridSize(self._grid_item_size)
            self._results.setSpacing(spacing)
            for index in range(self._results.count()):
                self._results.item(index).setSizeHint(self._grid_item_size)
            # Force IconMode to place every item before the next paint.  This
            # removes the transient three-cards-then-fill-in effect when the
            # column selector changes.
            self._results.doItemsLayout()
        finally:
            self._results.setUpdatesEnabled(updates_enabled)
        self._results.updateGeometry()
        if updates_enabled:
            self._results.viewport().update()

    def _schedule_grid_resize(self):
        """Repeat a grid pass after the parent layout has settled."""

        if self._grid_resize_pending:
            return
        self._grid_resize_pending = True
        QTimer.singleShot(0, self._run_scheduled_grid_resize)

    def _run_scheduled_grid_resize(self):
        self._grid_resize_pending = False
        self._resize_grid()

    def showEvent(self, event):
        super().showEvent(event)
        # The history popup is a non-activating tool window.  Explicitly
        # refresh and hide it on navigation restore so it cannot retain a
        # stale hidden state or hover/focus target from the previous page.
        self._refresh_search_history_popup()
        self._hide_search_history_popup()
        if self._tag_popup is not None:
            self._tag_popup.hide()
        QTimer.singleShot(0, self._resize_grid)

    def hideEvent(self, event):
        self._auto_search_timer.stop()
        self._hide_search_history_popup()
        if self._tag_popup is not None:
            self._tag_popup.hide()
        super().hideEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._resize_grid()
        self._schedule_grid_resize()
        self._fit_results_table_last_column()

    def _set_loading(self, loading: bool):
        self._loading = bool(loading)
        self._search_btn.setEnabled(not loading)
        self._reset_btn.setEnabled(not loading)
        self._previous_page_btn.setEnabled(not loading and self._current_page > 0)
        has_next = (
            self._current_page < self._last_page
            if self._last_page is not None
            else self._next_page is not None
        )
        self._next_page_btn.setEnabled(not loading and has_next)
        self._update_page_controls()
        if loading:
            self._status_label.setText(tr("Searching…", "搜索中…", "検索中…"))

    def _update_page_controls(self):
        if not hasattr(self, "_page_label"):
            return
        current = max(1, self._current_page + 1)
        if self._last_page is not None:
            total = max(current, self._last_page + 1)
            text = tr(f"Page {current} / {total}", f"第 {current} / {total} 页", f"{current} / {total} ページ")
        else:
            text = tr(f"Page {current}", f"第 {current} 页", f"{current} ページ")
        self._page_label.setText(text)
        if hasattr(self, "_jump_page_btn"):
            has_page_state = bool(
                self._last_page is not None
                or self._next_page is not None
                or self._total is not None
                or self._all_videos
                or self._all_authors
            )
            self._jump_page_btn.setEnabled(not self._loading and has_page_state)

    def _update_status(self, result: SearchPageResult | None = None):
        scope = result.scope if result is not None else str(self._scope_combo.currentData() or "videos")
        if scope == "authors":
            count = len(self._all_authors)
            self._status_label.setText(
                tr(f"Found {count} author(s)", f"找到 {count} 位作者", f"作者 {count} 件")
            )
        else:
            count = len(self._all_videos)
            total = f" / {self._total}" if self._total is not None else ""
            self._status_label.setText(
                tr(f"Found {count} video(s){total}", f"找到 {count} 个视频{total}", f"動画 {count} 件{total}")
            )

    def refresh_theme_styles(self):
        self._resize_grid()
        self._results.setStyleSheet(_search_grid_style())
        for key, item in self._item_by_key.items():
            item.setForeground(QColor("#f7fbff" if isDarkTheme() else "#17343b"))
            if key not in self._image_path_by_key:
                item.setIcon(self._placeholder_icon(key.split(":", 1)[0]))

    def _show_error(self, message: str):
        InfoBar.error(
            title=tr("Search failed", "搜索失败", "検索失敗"),
            content=message,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=4000,
            parent=self,
        )

    def _show_warning(self, message: str):
        InfoBar.warning(
            title=tr("Search warning", "搜索提示", "検索の注意"),
            content=message,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=4500,
            parent=self,
        )
