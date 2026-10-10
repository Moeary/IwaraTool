"""Build the item panel of the table view: action menus, filters, item table and cover grid."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QHBoxLayout,
    QListView,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
)

from qfluentwidgets import (
    Action,
    BodyLabel,
    ComboBox,
    FluentIcon,
    LineEdit,
    DropDownPushButton,
    PrimaryDropDownPushButton,
    PrimaryPushButton,
    PushButton,
    RoundMenu,
    SubtitleLabel,
    TableWidget,
    ToolButton,
)

from ...i18n import tr
from ..media.card_size import CARD_WIDTH_MIN, CardSizeControl, saved_card_width
from .components import (
    ResponsiveCoverList,
    _CONTROL_HEIGHT,
    _ROW_SPACING,
    _apply_fluent_scrollbars,
    _style_action_button,
    _style_inline_label,
    _thumbnail_list_style,
)
from ..rules.rule_picker import RulePicker
from ..theme import set_secondary_text
from ..ui_state import (
    ResponsiveFlowLayout,
    connect_splitter_saver,
    connect_table_column_saver,
    connect_table_width_saver,
    fit_table_last_column,
    restore_table_columns,
    restore_splitter_sizes,
    restore_table_widths,
)


class SubscriptionItemPanelLayoutMixin:
    """Build the item panel of the table view: action menus, filters, item table and cover grid."""

    def _build_item_summary_row(self, item_controls_layout: QVBoxLayout):
        item_summary_row = QHBoxLayout()
        item_summary_row.setSpacing(8)
        item_summary_row.addWidget(SubtitleLabel(tr("Videos", "视频列表", "動画一覧"), self))
        self._summary_label = BodyLabel("", self)
        self._summary_label.setWordWrap(True)
        set_secondary_text(self._summary_label)
        self._summary_label.setMinimumHeight(_CONTROL_HEIGHT)
        self._summary_label.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self._summary_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        item_summary_row.addWidget(self._summary_label, stretch=1)
        item_controls_layout.addLayout(item_summary_row)

    def _build_item_action_menus(self, item_controls_layout: QVBoxLayout):
        # Keep the action area compact: history, download and refresh operations
        # are grouped in menus instead of forcing several large buttons into rows.
        self._mark_downloaded_btn = PrimaryPushButton(self)
        self._restore_moved_btn = PrimaryPushButton(self)
        self._download_selected_btn = PrimaryPushButton(self)
        for hidden_btn in (self._mark_downloaded_btn, self._restore_moved_btn, self._download_selected_btn):
            hidden_btn.hide()

        history_menu = RoundMenu(parent=self)
        history_menu.addAction(Action(FluentIcon.HISTORY, tr("Show This Source", "显示这个作者的全部视频", "この購読元の全動画を表示"), self, triggered=self._show_selected_source_all_items))
        history_menu.addAction(Action(FluentIcon.ACCEPT, tr("Mark Selected as Downloaded", "将选中标为已下载", "選択を保存済みにする"), self, triggered=self._mark_selected_downloaded_moved))
        history_menu.addAction(Action(FluentIcon.RETURN, tr("Restore Selected Moved", "还原选中的已移走记录", "選択した移動済みを復元"), self, triggered=self._restore_selected_downloaded_moved))
        history_btn = DropDownPushButton(tr("History Actions", "历史操作", "履歴操作"), self, FluentIcon.HISTORY)
        history_btn.setMenu(history_menu)
        history_btn.setToolTip(tr("History and moved-record actions", "历史和已移走记录操作", "履歴・移動済み操作"))
        _style_action_button(history_btn, min_width=150)

        download_menu = RoundMenu(parent=self)
        download_menu.addAction(Action(FluentIcon.CHECKBOX, tr("Download Selected", "下载选中", "選択を保存"), self, triggered=self._download_selected_with_rule))
        download_menu.addAction(Action(FluentIcon.DOWNLOAD, tr("Download New", "下载新增", "新規を保存"), self, triggered=self._download_new_with_rule))
        download_menu.addAction(Action(FluentIcon.DOWNLOAD, tr("Download Visible", "下载当前列表", "表示分を保存"), self, triggered=self._download_visible_with_rule))
        download_btn = PrimaryDropDownPushButton(tr("Download Actions", "下载操作", "保存操作"), self, FluentIcon.DOWNLOAD)
        download_btn.setMenu(download_menu)
        download_btn.setToolTip(tr("Choose what to download", "选择下载范围", "保存範囲を選択"))
        _style_action_button(download_btn, min_width=150)

        refresh_menu = RoundMenu(parent=self)
        refresh_menu.addAction(
            Action(
                FluentIcon.SYNC,
                tr("Refresh Current List", "刷新当前列表", "現在の一覧を更新"),
                self,
                triggered=self._refresh_current_source,
            )
        )
        refresh_menu.addAction(
            Action(
                FluentIcon.PHOTO,
                tr("Refresh Covers", "刷新封面", "カバーを更新"),
                self,
                triggered=self._refresh_current_covers,
            )
        )
        refresh_menu.addAction(
            Action(
                FluentIcon.DOWNLOAD,
                tr("Cache All Source Covers", "缓存当前订阅源全部封面", "購読元の全カバーをキャッシュ"),
                self,
                triggered=self._cache_current_source_covers,
            )
        )
        self._refresh_current_btn = DropDownPushButton(
            tr("Refresh Actions", "刷新操作", "更新操作"),
            self,
            FluentIcon.SYNC,
        )
        self._refresh_current_btn.setMenu(refresh_menu)
        self._refresh_current_btn.setToolTip(
            tr(
                "Refresh this source's video list or covers",
                "刷新当前订阅源的视频列表或封面",
                "現在の購読元の動画一覧またはカバーを更新",
            )
        )
        _style_action_button(self._refresh_current_btn, min_width=150)

        item_actions = ResponsiveFlowLayout()
        item_actions.setSpacing(_ROW_SPACING)
        item_actions.addWidget(history_btn)
        item_actions.addWidget(download_btn)
        item_actions.addWidget(self._refresh_current_btn)
        item_controls_layout.addLayout(item_actions)

    def _build_download_rule_row(self, item_controls_layout: QVBoxLayout):
        download_options_row = ResponsiveFlowLayout()
        download_options_row.setSpacing(_ROW_SPACING)
        options_label = BodyLabel(tr("Download Rule", "下载规则", "保存ルール"), self)
        _style_inline_label(options_label)
        download_options_row.addWidget(options_label)
        self._rule_picker = RulePicker(self)
        download_options_row.addWidget(self._rule_picker)
        item_controls_layout.addLayout(download_options_row)

        # Keep the option widgets as an internal compatibility surface for the
        # existing global-setting synchronisation, but do not expose them as a
        # second wall of buttons. Named rules are the user-facing editor now.
        self._option_download_video_btn = self._make_download_option_button("", "")
        self._option_mark_downloaded_btn = self._make_download_option_button("", "")
        self._option_download_thumb_btn = self._make_download_option_button("", "")
        self._option_collect_nfo_btn = self._make_download_option_button("", "")
        for option_btn in (
            self._option_download_video_btn,
            self._option_mark_downloaded_btn,
            self._option_download_thumb_btn,
            self._option_collect_nfo_btn,
        ):
            option_btn.hide()

    def _build_title_filter_row(self, item_controls_layout: QVBoxLayout):
        title_filter_row = ResponsiveFlowLayout()
        title_filter_row.setSpacing(_ROW_SPACING)
        title_filter_label = BodyLabel(tr("Title Filter", "标题筛选", "タイトルフィルター"), self)
        _style_inline_label(title_filter_label)
        title_filter_row.addWidget(title_filter_label)
        self._title_filter_mode_btn = PushButton(tr("Simple", "简单搜索", "簡易検索"), self)
        self._title_filter_mode_btn.setCheckable(True)
        self._title_filter_mode_btn.setFixedSize(120, _CONTROL_HEIGHT)
        self._title_filter_mode_btn.setToolTip(
            tr("Switch to regex mode", "切换到正则模式", "正規表現モードに切替")
        )
        self._title_filter_mode_btn.toggled.connect(self._on_title_filter_mode_toggled)
        title_filter_row.addWidget(self._title_filter_mode_btn)
        self._title_search_edit = LineEdit(self)
        self._title_search_edit.setPlaceholderText(
            tr("Search titles as you type…", "输入标题关键词，实时筛选…", "タイトルを入力して絞り込み…")
        )
        self._title_search_edit.setClearButtonEnabled(True)
        self._title_search_edit.setMinimumWidth(330)
        self._title_search_edit.textChanged.connect(self._apply_item_filters)
        title_filter_row.addWidget(self._title_search_edit)
        item_controls_layout.addLayout(title_filter_row)

    def _build_item_filter_row(self, item_controls_layout: QVBoxLayout):
        item_filter_row = ResponsiveFlowLayout()
        item_filter_row.setSpacing(_ROW_SPACING)
        install_label = BodyLabel(tr("Download Status", "下载状态", "保存状態"), self)
        _style_inline_label(install_label)
        item_filter_row.addWidget(install_label)
        self._install_filter_combo = ComboBox(self)
        self._install_filter_combo.addItems(
            [
                tr("All", "全部", "全て"),
                tr("Ready", "可下载", "保存可能"),
                tr("Not Downloadable", "不可下载", "保存不可"),
                tr("Downloaded", "已下载", "保存済み"),
                tr("Moved", "已移走", "移動済み"),
                tr("Queued", "已入队", "キュー内"),
            ]
        )
        self._install_filter_combo.setFixedSize(150, _CONTROL_HEIGHT)
        self._install_filter_combo.currentIndexChanged.connect(self._apply_item_filters)
        item_filter_row.addWidget(self._install_filter_combo)

        new_label = BodyLabel(tr("New", "新增", "新規"), self)
        _style_inline_label(new_label)
        item_filter_row.addWidget(new_label)
        self._new_filter_combo = ComboBox(self)
        self._new_filter_combo.addItems(
            [
                tr("All", "全部", "全て"),
                tr("New Only", "仅新增", "新規のみ"),
                tr("Not New", "非新增", "新規以外"),
            ]
        )
        self._new_filter_combo.setFixedSize(120, _CONTROL_HEIGHT)
        self._new_filter_combo.currentIndexChanged.connect(self._apply_item_filters)
        item_filter_row.addWidget(self._new_filter_combo)

        sort_label = BodyLabel(tr("Sort", "排序", "並び順"), self)
        _style_inline_label(sort_label)
        item_filter_row.addWidget(sort_label)
        self._item_sort_combo = ComboBox(self)
        self._item_sort_combo.addItems(
            [
                tr("Date Desc", "时间倒序", "日付降順"),
                tr("Date Asc", "时间升序", "日付昇順"),
                tr("New First", "新增优先", "新規優先"),
                tr("Title A-Z", "标题 A-Z", "タイトル A-Z"),
            ]
        )
        self._item_sort_combo.setFixedSize(130, _CONTROL_HEIGHT)
        self._item_sort_combo.currentIndexChanged.connect(self._apply_item_filters)
        item_filter_row.addWidget(self._item_sort_combo)

        view_label = BodyLabel(tr("View", "视图", "表示"), self)
        _style_inline_label(view_label)
        item_filter_row.addWidget(view_label)
        self._item_view_combo = ComboBox(self)
        self._item_view_combo.addItems(
            [
                tr("List", "列表", "リスト"),
                tr("Covers", "封面", "カバー"),
            ]
        )
        self._item_view_combo.setFixedSize(100, _CONTROL_HEIGHT)
        self._item_view_combo.currentIndexChanged.connect(self._on_item_view_changed)
        item_filter_row.addWidget(self._item_view_combo)

        # Same cover-size slider as Home, Search and the overview.
        self._item_card_size = CardSizeControl(self)
        self._item_card_size.size_changed.connect(self._on_item_card_size_changed)
        self._item_card_min_width = max(CARD_WIDTH_MIN, int(saved_card_width() * 0.8))
        item_filter_row.addWidget(self._item_card_size)

        self._item_columns_btn = ToolButton(FluentIcon.SETTING, self)
        self._item_columns_btn.setFixedSize(36, _CONTROL_HEIGHT)
        self._item_columns_btn.setToolTip(tr("Fields", "字段设置", "列設定"))
        self._item_columns_btn.clicked.connect(self._configure_item_columns)
        item_filter_row.addWidget(self._item_columns_btn)
        item_controls_layout.addLayout(item_filter_row)

    def _build_item_table(self):
        self._item_table = TableWidget(self)
        self._item_table.setColumnCount(11)
        self._item_table.setHorizontalHeaderLabels(
            [
                tr("State", "状态", "状態"),
                tr("Reason", "原因", "理由"),
                tr("New", "新增", "新規"),
                tr("Title", "标题", "タイトル"),
                tr("Author", "作者", "作者"),
                tr("Published", "发布时间", "公開日"),
                "ID",
                tr("Source URL", "来源URL", "元URL"),
                tr("Page", "页面", "ページ"),
                tr("Folder", "文件夹", "フォルダー"),
                tr("File", "文件", "ファイル"),
            ]
        )
        self._item_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._item_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._item_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._item_table.setAlternatingRowColors(True)
        self._item_table.setBorderVisible(True)
        self._item_table.setBorderRadius(8)
        self._item_table.setWordWrap(False)
        self._item_table.verticalHeader().setVisible(False)
        self._item_table.verticalHeader().setDefaultSectionSize(38)
        self._item_table.itemDoubleClicked.connect(lambda item: self._open_item_from_cell(item, open_file=None))
        self._item_table.cellClicked.connect(self._on_item_cell_clicked)
        self._item_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._item_table.customContextMenuRequested.connect(self._show_item_table_context_menu)
        item_header = self._item_table.horizontalHeader()
        item_header.setHighlightSections(False)
        item_header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        item_widths = {
            self._ITEM_STATE: 90,
            self._ITEM_REASON: 260,
            self._ITEM_NEW: 58,
            self._ITEM_TITLE: 660,
            self._ITEM_AUTHOR: 140,
            self._ITEM_PUBLISHED: 130,
            self._ITEM_ID: 130,
            self._ITEM_SOURCE_URL: 260,
            self._ITEM_URL: 68,
            self._ITEM_FOLDER: 68,
            self._ITEM_FILE: 68,
        }
        restore_table_widths(self._item_table, "subscription_item_widths_v2", item_widths)
        connect_table_width_saver(self._item_table, "subscription_item_widths_v2")
        restore_table_columns(self._item_table, "subscription_item_table_v2")
        connect_table_column_saver(self._item_table, "subscription_item_table_v2")
        self._item_table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        item_header.sectionResized.connect(lambda *_args: fit_table_last_column(self._item_table))
        item_header.sectionMoved.connect(lambda *_args: fit_table_last_column(self._item_table))
        self._item_table.selectionModel().selectionChanged.connect(lambda *_args: self._update_selection_actions())

    def _build_thumbnail_list(self):
        self._thumbnail_list = ResponsiveCoverList(self)
        self._thumbnail_list.setObjectName("SubscriptionThumbnailList")
        self._thumbnail_list.setStyleSheet(_thumbnail_list_style())
        self._thumbnail_list.viewport().setAutoFillBackground(False)
        self._thumbnail_list.setViewMode(QListView.ViewMode.IconMode)
        self._thumbnail_list.setResizeMode(QListView.ResizeMode.Adjust)
        self._thumbnail_list.setMovement(QListView.Movement.Static)
        self._thumbnail_list.setFlow(QListView.Flow.LeftToRight)
        self._thumbnail_list.setWrapping(True)
        self._thumbnail_list.setWordWrap(True)
        self._thumbnail_list.setUniformItemSizes(True)
        self._thumbnail_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._thumbnail_list.setIconSize(QSize(256, 144))
        self._thumbnail_list.setGridSize(QSize(286, 232))
        self._thumbnail_list.setSpacing(8)
        self._thumbnail_list.itemDoubleClicked.connect(self._on_thumbnail_item_activated)
        self._thumbnail_list.itemSelectionChanged.connect(self._update_selection_actions)
        self._thumbnail_list.resized.connect(self._schedule_thumbnail_grid_update)
        self._thumbnail_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._thumbnail_list.customContextMenuRequested.connect(self._show_thumbnail_context_menu)

    def _build_item_stack(self, item_content_layout: QVBoxLayout):
        self._item_stack = QStackedWidget(self)
        self._item_stack.addWidget(self._item_table)
        self._item_stack.addWidget(self._thumbnail_list)
        item_content_layout.addWidget(self._item_stack, stretch=1)
        _apply_fluent_scrollbars(self._item_table)
        _apply_fluent_scrollbars(self._thumbnail_list)
        restore_splitter_sizes(self._item_content_splitter, "subscription_item_content_splitter_sizes", [390, 1000])
        connect_splitter_saver(self._item_content_splitter, "subscription_item_content_splitter_sizes")
