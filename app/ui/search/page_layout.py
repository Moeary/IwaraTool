"""Build the search page widgets: query card, result header and result views."""

from __future__ import annotations

import json

from PySide6.QtCore import Qt, QSize, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHeaderView,
    QHBoxLayout,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    ComboBox,
    FluentIcon,
    ListWidget,
    PrimaryPushButton,
    PushButton,
    TableWidget,
    ToolButton,
)

from ...config import app_config
from ...core.rating import RATING_ALL, UI_RATING_KEY, normalize_rating, rating_options
from ...i18n import tr
from ..rules.rule_picker import RulePicker
from ..card_list import CardGridList
from ..click_dispatch import ClickDispatcher
from .widgets import SearchHistoryPopup, SearchKeywordEdit, TagSuggestionPopup
from .grid_style import _search_grid_style
from ..ui_state import (
    connect_table_column_saver,
    connect_table_width_saver,
    restore_table_columns,
    restore_table_widths,
)
from ..media.card_size import CardSizeControl, saved_card_width
from ..shortcuts import attach_hint
from ..theme import CARD_MARGINS, PAGE_MARGINS, set_secondary_text

from .click_filter import _ResultClickFilter
from .results_layout import _DEFAULT_GRID_HEIGHT, _VIDEO_ICON_SIZE


class SearchPageLayoutMixin:
    """Build the search page widgets: query card, result header and result views."""

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(*PAGE_MARGINS)
        root.setSpacing(10)
        root.addWidget(self._build_query_card())
        self._connect_global_signals()
        self._build_query_popups()
        root.addLayout(self._build_result_header())
        root.addWidget(self._build_result_card(), 1)
        self._apply_initial_state()

    def _build_query_card(self) -> CardWidget:
        query_card = CardWidget(self)
        query_layout = QVBoxLayout(query_card)
        query_layout.setContentsMargins(*CARD_MARGINS)
        query_layout.setSpacing(10)
        query_layout.addLayout(self._build_keyword_row(query_card))
        query_layout.addLayout(self._build_options_row(query_card))

        # Only shown while browsing one author's works; the generic per-scope
        # explanations live in the keyword box's tooltip instead.
        self._scope_hint = CaptionLabel("", query_card)
        self._scope_hint.setWordWrap(True)
        self._scope_hint.hide()
        set_secondary_text(self._scope_hint)
        query_layout.addWidget(self._scope_hint)
        self._query_card = query_card
        return query_card

    def _build_keyword_row(self, query_card: QWidget) -> QHBoxLayout:
        # Row 1: where to search, what to search for, go.  The combo boxes
        # show their current value, so they carry no separate captions.
        keyword_row = QHBoxLayout()
        keyword_row.setSpacing(8)
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
        self._source_combo.setMinimumWidth(168)
        self._source_combo.setToolTip(tr("Search source", "数据源", "検索ソース"))
        self._set_combo_data(self._source_combo, app_config.search_default_source)
        self._source_combo.currentIndexChanged.connect(self._on_source_combo_changed)
        keyword_row.addWidget(self._source_combo)

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
        self._keyword_edit.textEdited.connect(self._clear_author_navigation)
        self._keyword_edit.textEdited.connect(self._unpin_source)
        self._keyword_edit.textChanged.connect(self._sync_sort_options)
        keyword_row.addWidget(self._keyword_edit, 1)

        self._search_btn = PrimaryPushButton(tr("Search", "搜索", "検索"), query_card, FluentIcon.SEARCH)
        self._search_btn.setMinimumWidth(104)
        self._search_btn.clicked.connect(self._start_search)
        keyword_row.addWidget(self._search_btn)

        self._reset_btn = ToolButton(FluentIcon.CANCEL, query_card)
        self._reset_btn.setToolTip(tr("Reset the search", "重置搜索", "検索をリセット"))
        self._reset_btn.clicked.connect(self._reset_filters)
        keyword_row.addWidget(self._reset_btn)
        return keyword_row

    def _build_options_row(self, query_card: QWidget) -> QHBoxLayout:
        # Row 2: scope / sort / rating on the left, the view switch on the right.
        options_row = QHBoxLayout()
        options_row.setSpacing(8)
        self._scope_combo = ComboBox(query_card)
        self._scope_items = [
            (tr("Videos", "视频", "動画"), "videos"),
            (tr("Images", "图片", "画像"), "images"),
            (tr("Authors", "作者", "作者"), "authors"),
            (tr("Tags", "标签搜索", "タグ検索"), "tags"),
            (tr("Playlists", "播放列表", "プレイリスト"), "playlists"),
        ]
        for text, data in self._scope_items:
            self._add_combo_item(self._scope_combo, text, data)
        self._scope_combo.setMinimumWidth(120)
        self._scope_combo.setToolTip(tr("What to search", "搜索类型", "検索対象"))
        self._scope_combo.currentIndexChanged.connect(self._on_scope_changed)
        options_row.addWidget(self._scope_combo)

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
        self._sort_combo.setMinimumWidth(120)
        self._sort_combo.setToolTip(tr("Sort order", "排序", "並び順"))
        self._sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        options_row.addWidget(self._sort_combo)
        options_row.addWidget(self._build_rating_selector(query_card))
        options_row.addStretch(1)

        self._view_combo = self._make_combo(
            [
                (tr("Grid", "网格", "グリッド"), "grid"),
                (tr("List", "列表", "リスト"), "list"),
            ],
            query_card,
        )
        self._view_combo.setFixedWidth(92)
        self._view_combo.setToolTip(tr("Result view", "结果视图", "表示"))
        saved_view = str(app_config.get_ui_value("search_view_mode_v1", "grid") or "grid")
        self._view_combo.setCurrentIndex(1 if saved_view == "list" else 0)
        self._view_combo.currentIndexChanged.connect(self._on_view_changed)
        options_row.addWidget(self._view_combo)
        # The same cover-size slider as Home and Subscriptions: one control,
        # one saved size, instead of a separate "columns" number here.
        self._card_size_control = CardSizeControl(query_card)
        options_row.addWidget(self._card_size_control)
        self._card_min_width = saved_card_width()
        self._card_size_control.size_changed.connect(self._on_card_size_changed)
        self._result_fields_btn = ToolButton(FluentIcon.SETTING, query_card)
        self._result_fields_btn.setToolTip(tr("Fields", "字段设置", "列設定"))
        self._result_fields_btn.clicked.connect(self._configure_result_columns)
        options_row.addWidget(self._result_fields_btn)
        return options_row

    def _build_rating_selector(self, query_card: QWidget) -> QWidget:
        # SFW / NSFW, shared with the Home page. Oreno3D cards carry no
        # rating, so the selector is only offered for the Iwara source.
        self._rating_group = QWidget(query_card)
        rating_layout = QHBoxLayout(self._rating_group)
        rating_layout.setContentsMargins(0, 0, 0, 0)
        rating_layout.setSpacing(0)
        self._rating_combo = self._make_combo(rating_options(), self._rating_group)
        self._rating_combo.setMinimumWidth(104)
        self._rating_combo.setToolTip(
            tr(
                "Content rating. SFW shows general-rated posts only, NSFW shows R-18 (ecchi) posts only.",
                "内容分级。SFW 仅显示全年龄作品，NSFW 仅显示 R-18（ecchi）作品。",
                "コンテンツ区分。SFWは全年齢向けのみ、NSFWはR-18（ecchi）のみを表示します。",
            )
        )
        self._set_combo_data(
            self._rating_combo, normalize_rating(app_config.get_ui_value(UI_RATING_KEY, RATING_ALL))
        )
        self._rating_combo.currentIndexChanged.connect(self._on_rating_changed)
        rating_layout.addWidget(self._rating_combo)
        return self._rating_group

    def _build_query_popups(self):
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

    def _build_result_header(self) -> QHBoxLayout:
        # Result bar: pager and status on the left, what to do with the
        # selection on the right.
        result_header = QHBoxLayout()
        result_header.setSpacing(8)
        result_header.addWidget(self._build_pagination())

        self._status_label = CaptionLabel("", self)
        self._status_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        set_secondary_text(self._status_label)
        result_header.addWidget(self._status_label, 1)

        # The rule decides naming and filtering of whatever is added, so it
        # sits right next to the buttons that add.
        rule_group = QWidget(self)
        rule_group_layout = QHBoxLayout(rule_group)
        rule_group_layout.setContentsMargins(0, 0, 0, 0)
        rule_group_layout.setSpacing(0)
        self._rule_picker = RulePicker(rule_group, scope="search")
        self._rule_picker.setToolTip(
            tr(
                "Download rule: controls filtering, naming and download behavior.",
                "下载规则：统一控制筛选、命名和下载行为。",
                "ダウンロードルール：フィルター・命名・保存動作を統一します。",
            )
        )
        rule_group_layout.addWidget(self._rule_picker)
        self._rule_card = rule_group
        result_header.addWidget(rule_group)

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
        self._toggle_search_controls_btn = ToolButton(self)
        self._toggle_search_controls_btn.clicked.connect(self._toggle_search_controls)
        result_header.addWidget(self._toggle_search_controls_btn)

        # Paging, focus and the other search keys come from the shortcut catalogue
        # (see shortcut_bindings), so rebinding them in Settings really takes effect.
        attach_hint(self._previous_page_btn, tr("Previous page", "上一页", "前のページ"), "search_prev_page")
        attach_hint(self._next_page_btn, tr("Next page", "下一页", "次のページ"), "search_next_page")
        attach_hint(self._reset_btn, tr("Reset the search", "重置搜索", "検索をリセット"), "search_reset")
        return result_header

    def _build_pagination(self) -> QWidget:
        pagination = QWidget(self)
        pagination_layout = QHBoxLayout(pagination)
        pagination_layout.setContentsMargins(0, 0, 0, 0)
        pagination_layout.setSpacing(4)
        self._previous_page_btn = ToolButton(pagination)
        self._previous_page_btn.setIcon(FluentIcon.LEFT_ARROW)
        self._previous_page_btn.setToolTip(
            tr("Previous page (Alt+Left)", "上一页（Alt+←）", "前のページ（Alt+←）")
        )
        self._previous_page_btn.clicked.connect(self._go_previous_page)
        pagination_layout.addWidget(self._previous_page_btn)
        self._page_label = BodyLabel("", pagination)
        self._page_label.setMinimumWidth(84)
        self._page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pagination_layout.addWidget(self._page_label)
        self._next_page_btn = ToolButton(pagination)
        self._next_page_btn.setIcon(FluentIcon.RIGHT_ARROW)
        self._next_page_btn.setToolTip(
            tr("Next page (Alt+Right)", "下一页（Alt+→）", "次のページ（Alt+→）")
        )
        self._next_page_btn.clicked.connect(self._go_next_page)
        pagination_layout.addWidget(self._next_page_btn)
        self._jump_page_btn = ToolButton(FluentIcon.SEND, pagination)
        self._jump_page_btn.setToolTip(
            tr("Jump to a page", "输入页码并跳转", "ページ番号を入力して移動")
        )
        self._jump_page_btn.clicked.connect(self._jump_to_page)
        pagination_layout.addWidget(self._jump_page_btn)
        return pagination

    def _build_result_card(self) -> CardWidget:
        result_card = CardWidget(self)
        result_layout = QVBoxLayout(result_card)
        result_layout.setContentsMargins(0, 0, 0, 0)
        self._result_clicks = ClickDispatcher(self._perform_result_click, self)
        self._results_stack = QStackedWidget(result_card)
        self._results_stack.addWidget(self._build_results_grid(result_card))
        # Built inside the stack: moving a finished table into another parent resets its column widths.
        self._results_stack.addWidget(self._build_results_table(self._results_stack))
        result_layout.addWidget(self._results_stack)
        return result_card

    def _build_results_grid(self, result_card: QWidget) -> ListWidget:
        self._results = CardGridList(result_card)
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
        _ResultClickFilter(self._results, self._result_clicks)
        self._results.itemActivated.connect(self._activate_result)
        self._results.itemSelectionChanged.connect(self._sync_selection_buttons)
        self._results.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._results.customContextMenuRequested.connect(self._show_context_menu)
        return self._results

    def _build_results_table(self, result_card: QWidget) -> TableWidget:
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
                tr("Download status", "下载状态", "ダウンロード状態"),
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
        self._restore_results_table_layout()
        _ResultClickFilter(self._results_table, self._result_clicks)
        self._results_table.itemActivated.connect(self._activate_result)
        self._results_table.itemSelectionChanged.connect(self._sync_selection_buttons)
        self._results_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._results_table.customContextMenuRequested.connect(self._show_context_menu)
        return self._results_table

    def _restore_results_table_layout(self):
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
                12: 160,
            },
        )
        connect_table_width_saver(self._results_table, "search_result_widths_v2")
        restore_table_columns(
            self._results_table,
            "search_result_table_v2",
            default_visible=[0, 1, 2, 3, 4, 5, 7, 8, 12],
            default_order=[0, 1, 2, 12, 3, 4, 5, 6, 7, 8, 9, 10, 11],
        )
        # Old layouts have no entry for this appended logical column. Show
        # it once on upgrade while keeping all existing field preferences.
        try:
            saved_layout = json.loads(str(app_config.get_ui_value("search_result_table_v2_columns", "") or "{}"))
        except (TypeError, ValueError):
            saved_layout = {}
        saved_order = saved_layout.get("order", []) if isinstance(saved_layout, dict) else []
        if not isinstance(saved_order, list) or 12 not in saved_order:
            self._results_table.setColumnHidden(12, False)
        connect_table_column_saver(self._results_table, "search_result_table_v2")

    def _apply_initial_state(self):
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
