"""Subscription Interface — sources, update checks, and batch enqueue."""
from __future__ import annotations

import os
import re
from typing import Any

from PySide6.QtCore import QSize, QTimer, Qt, QThread
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHeaderView,
    QHBoxLayout,
    QListView,
    QListWidgetItem,
    QSizePolicy,
    QScrollArea,
    QStackedWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from qfluentwidgets import (
    Action,
    BodyLabel,
    CardWidget,
    ComboBox,
    FluentIcon,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    PrimaryDropDownPushButton,
    PrimaryPushButton,
    ProgressBar,
    RoundMenu,
    SubtitleLabel,
    TableWidget,
    TitleLabel,
    ToolButton,
    isDarkTheme,
)

from ..config import app_config
from ..core.manager import download_manager
from ..i18n import tr
from ..signal_bus import signal_bus
from .subscription_actions import SubscriptionActionsMixin
from .subscription_helpers import (
    _date_only,
    _detect_source_input,
    _ellipsize,
    _extract_author_key,
    _extract_playlist_key,
    _item_date_key,
    _item_not_downloadable,
    _item_state_text,
    _open_url,
    _parse_iwara_path,
    _parse_ui_bool,
    _source_origin_color,
    _source_origin_label,
    _source_search_text,
    _source_sort_key,
    _source_sort_label,
    _source_type_label,
    _source_url,
    _state_color,
    _task_status_from_value,
    _title_matcher,
    _title_matches_keywords,
    _video_url,
    _split_title_keywords,
)
from .subscription_components import (
    ResponsiveCoverList,
    SubscriptionAvatarWorker,
    SubscriptionEnqueueWorker,
    SubscriptionImportAuthorsWorker,
    SubscriptionRefreshWorker,
    SubscriptionThumbnailWorker,
    _CONTROL_HEIGHT,
    _DEFAULT_COVER_DOWNLOAD_CONCURRENCY,
    _DEFAULT_SUBSCRIPTION_GRID_COLUMNS,
    _FluentContentSplitter,
    _MAX_COVER_DOWNLOAD_CONCURRENCY,
    _MAX_SUBSCRIPTION_GRID_COLUMNS,
    _ROW_SPACING,
    _SOURCE_SORT_FIELDS,
    _SOURCE_SORT_OPTIONS,
    _apply_fluent_scrollbars,
    _grid_text_height,
    _style_action_button,
    _style_content_splitter,
    _style_inline_label,
    _thumbnail_list_style,
)
from .rules_page import RulePicker
from .ui_state import (
    ResponsiveFlowLayout,
    connect_splitter_saver,
    connect_table_column_saver,
    connect_table_width_saver,
    fit_table_last_column,
    open_table_column_dialog,
    restore_table_columns,
    restore_splitter_sizes,
    restore_table_widths,
)
from .worker_lifecycle import stop_qthreads


class SubscriptionInterface(SubscriptionActionsMixin, QWidget):
    """Page for tracking subscription updates and queueing downloads."""

    _SRC_AVATAR = 0
    _SRC_STATE = 1
    _SRC_TYPE = 2
    _SRC_TITLE = 3
    _SRC_NEW = 4
    _SRC_UNDOWNLOADED = 5
    _SRC_ITEMS = 6
    _SRC_CHECKED = 7
    _SRC_CREATED = 8
    _SRC_KEY = 9
    _SRC_URL = 10
    _SRC_OPEN = 11

    _ITEM_STATE = 0
    _ITEM_REASON = 1
    _ITEM_NEW = 2
    _ITEM_TITLE = 3
    _ITEM_AUTHOR = 4
    _ITEM_PUBLISHED = 5
    _ITEM_ID = 6
    _ITEM_SOURCE_URL = 7
    _ITEM_URL = 8
    _ITEM_FOLDER = 9
    _ITEM_FILE = 10

    _RENDER_BATCH_SIZE = 80

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("SubscriptionInterface")
        self._worker: SubscriptionRefreshWorker | None = None
        self._import_worker: SubscriptionImportAuthorsWorker | None = None
        self._enqueue_worker: SubscriptionEnqueueWorker | None = None
        self._avatar_worker: SubscriptionAvatarWorker | None = None
        self._thumbnail_worker: SubscriptionThumbnailWorker | None = None
        self._shutting_down = False
        self._avatar_requested_source_ids: set[int] = set()
        self._thumbnail_requested_video_ids: set[str] = set()
        self._thumbnail_force_refresh_ids: set[str] = set()
        self._thumbnail_force_refresh_pending = False
        self._thumbnail_items_by_video_id: dict[str, list[QListWidgetItem]] = {}
        self._all_sources: list[dict[str, Any]] = []
        self._sources: list[dict[str, Any]] = []
        self._all_items: list[dict[str, Any]] = []
        self._visible_items: list[dict[str, Any]] = []
        self._current_source_id: int | None = None
        self._pending_video_ids: set[str] = set()
        self._title_filter_error = ""
        self._refresh_info_bar: InfoBar | None = None
        self._refresh_progress: ProgressBar | None = None
        self._source_sort_field = str(
            app_config.get_ui_value("subscription_source_sort_field_v1", "title") or "title"
        ).strip().casefold()
        if self._source_sort_field not in _SOURCE_SORT_FIELDS:
            self._source_sort_field = "title"
        self._source_sort_desc = _parse_ui_bool(
            app_config.get_ui_value("subscription_source_sort_desc_v1", "0")
        )
        self._source_render_index = 0
        self._item_render_index = 0
        self._items_refresh_pending = False
        self._thumbnail_grid_resize_pending = False
        self._syncing_download_options = False
        self._source_render_timer = QTimer(self)
        self._source_render_timer.setInterval(0)
        self._source_render_timer.timeout.connect(self._render_source_batch)
        self._item_render_timer = QTimer(self)
        self._item_render_timer.setInterval(0)
        self._item_render_timer.timeout.connect(self._render_item_batch)
        self._build_ui()
        self._load_sources()
        signal_bus.tasks_added.connect(self._on_tasks_changed)
        signal_bus.task_added.connect(self._on_task_changed)
        signal_bus.task_status_changed.connect(self._on_task_status_changed)
        signal_bus.tasks_removed.connect(self._on_tasks_changed)
        signal_bus.task_removed.connect(self._on_task_changed)
        signal_bus.download_options_changed.connect(self._sync_download_option_buttons)
        signal_bus.subscription_source_added.connect(self._on_subscription_source_added)
        self._sync_download_option_buttons()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(36, 24, 36, 16)
        root.setSpacing(12)

        title_row = QHBoxLayout()
        title_row.addWidget(TitleLabel(tr("Subscriptions", "订阅页", "購読"), self))
        title_row.addStretch()
        root.addLayout(title_row)

        splitter = _FluentContentSplitter(Qt.Orientation.Horizontal, self)
        self._splitter = splitter
        self._splitter_is_vertical = False
        self._source_panel_visible = True
        self._items_panel_visible = True
        self._last_splitter_sizes = [1000, 1000]
        splitter.splitterMoved.connect(self._on_splitter_moved)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(10)
        _style_content_splitter(splitter)
        root.addWidget(splitter, stretch=1)

        left_panel = CardWidget(self)
        self._source_panel = left_panel
        left_panel.setObjectName("SubscriptionSourcesCard")
        left_panel.setMinimumWidth(0)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(16, 14, 16, 14)
        left_layout.setSpacing(_ROW_SPACING)
        splitter.addWidget(left_panel)

        right_panel = CardWidget(self)
        self._items_panel = right_panel
        right_panel.setObjectName("SubscriptionItemsCard")
        right_panel.setMinimumWidth(0)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(16, 14, 16, 14)
        right_layout.setSpacing(_ROW_SPACING)
        splitter.addWidget(right_panel)

        self._item_content_splitter = _FluentContentSplitter(Qt.Orientation.Vertical, right_panel)
        self._item_content_splitter.setObjectName("SubscriptionItemContentSplitter")
        self._item_content_splitter.setChildrenCollapsible(True)
        self._item_content_splitter.setHandleWidth(10)
        self._item_content_splitter.setToolTip(
            tr(
                "Drag this handle to resize or collapse the video controls.",
                "拖动此分隔条调整或隐藏视频控制区。",
                "このハンドルをドラッグして動画操作領域を調整・折りたたみます。",
            )
        )
        _style_content_splitter(self._item_content_splitter)
        item_controls_scroll = QScrollArea(self._item_content_splitter)
        self._item_controls_scroll = item_controls_scroll
        item_controls_scroll.setObjectName("SubscriptionItemControlsScroll")
        item_controls_scroll.setFrameShape(QFrame.Shape.NoFrame)
        item_controls_scroll.setWidgetResizable(True)
        item_controls_scroll.setMinimumSize(0, 0)
        item_controls_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        item_controls_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        item_controls_scroll.setStyleSheet(
            "QScrollArea#SubscriptionItemControlsScroll { background: transparent; border: none; }"
            "QScrollArea#SubscriptionItemControlsScroll > QWidget > QWidget { background: transparent; }"
        )
        item_controls_scroll.viewport().setAutoFillBackground(False)
        _apply_fluent_scrollbars(item_controls_scroll)
        item_controls_panel = QWidget()
        item_controls_panel.setObjectName("SubscriptionItemControlsPanel")
        item_controls_panel.setMinimumHeight(0)
        item_controls_layout = QVBoxLayout(item_controls_panel)
        item_controls_layout.setContentsMargins(0, 0, 0, 0)
        item_controls_layout.setSpacing(_ROW_SPACING)
        item_controls_scroll.setWidget(item_controls_panel)
        item_content_panel = QWidget(self._item_content_splitter)
        item_content_panel.setObjectName("SubscriptionItemContentPanel")
        item_content_panel.setMinimumHeight(0)
        item_content_layout = QVBoxLayout(item_content_panel)
        item_content_layout.setContentsMargins(0, 0, 0, 0)
        item_content_layout.setSpacing(0)
        self._item_content_splitter.addWidget(item_controls_scroll)
        self._item_content_splitter.addWidget(item_content_panel)
        self._item_content_splitter.setStretchFactor(0, 0)
        self._item_content_splitter.setStretchFactor(1, 1)
        right_layout.addWidget(self._item_content_splitter, stretch=1)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        restore_splitter_sizes(splitter, "subscription_splitter_sizes_v2", [1000, 1000])
        connect_splitter_saver(splitter, "subscription_splitter_sizes_v2")
        self._last_splitter_sizes = list(splitter.sizes()) or [1000, 1000]

        self._toggle_sources_btn = ToolButton(self)
        self._toggle_items_btn = ToolButton(self)
        for button in (self._toggle_sources_btn, self._toggle_items_btn):
            button.setFixedSize(40, _CONTROL_HEIGHT)
        self._toggle_sources_btn.clicked.connect(self._toggle_source_panel)
        self._toggle_items_btn.clicked.connect(self._toggle_item_panel)
        title_row.addWidget(self._toggle_sources_btn)
        title_row.addWidget(self._toggle_items_btn)
        self._update_panel_toggle_buttons()

        source_header = QHBoxLayout()
        source_header.setSpacing(8)
        source_header.addWidget(SubtitleLabel(tr("Subscriptions", "订阅源", "購読元"), self))
        source_header.addStretch()
        left_layout.addLayout(source_header)

        source_actions = ResponsiveFlowLayout()
        source_actions.setSpacing(_ROW_SPACING)
        import_authors_btn = PrimaryPushButton(tr("Import Followed", "导入关注作者", "フォローを取込"), self, FluentIcon.PEOPLE)
        _style_action_button(import_authors_btn)
        import_authors_btn.clicked.connect(self._import_followed_authors)
        source_actions.addWidget(import_authors_btn)

        add_feed_btn = PrimaryPushButton(tr("Account Feed", "账号订阅流", "購読フィード"), self, FluentIcon.HISTORY)
        _style_action_button(add_feed_btn)
        add_feed_btn.clicked.connect(self._add_following_feed)
        source_actions.addWidget(add_feed_btn)

        add_source_btn = PrimaryPushButton(tr("Add Author / Playlist", "添加作者/播放列表", "作者/リストを追加"), self, FluentIcon.PEOPLE)
        _style_action_button(add_source_btn)
        add_source_btn.clicked.connect(self._add_source)
        source_actions.addWidget(add_source_btn)

        delete_source_btn = PrimaryPushButton(
            tr("Delete Subscription", "删除订阅", "購読を削除"),
            self,
            FluentIcon.DELETE,
        )
        _style_action_button(delete_source_btn)
        delete_source_btn.clicked.connect(self._delete_selected_source)
        source_actions.addWidget(delete_source_btn)

        self._refresh_all_btn = PrimaryPushButton(tr("Refresh All", "刷新全部", "全件更新"), self, FluentIcon.SYNC)
        _style_action_button(self._refresh_all_btn)
        self._refresh_all_btn.clicked.connect(self._refresh_all)
        source_actions.addWidget(self._refresh_all_btn)
        left_layout.addLayout(source_actions)

        storage = download_manager.get_subscription_storage_info()
        db_name = os.path.basename(str(storage.get("db_path", "") or "")) or "history.db"
        storage_label = BodyLabel(tr("Saved locally", "本地保存", "ローカル保存") + f": {db_name}", self)
        _style_inline_label(storage_label)
        storage_label.setToolTip(
            tr(
                f"DB: {storage.get('db_path', '')}\nBackup: {storage.get('backup_path', '')}",
                f"数据库: {storage.get('db_path', '')}\n备份: {storage.get('backup_path', '')}",
                f"DB: {storage.get('db_path', '')}\nバックアップ: {storage.get('backup_path', '')}",
            )
        )
        source_meta_row = ResponsiveFlowLayout()
        source_meta_row.setSpacing(_ROW_SPACING)
        source_meta_row.addWidget(storage_label)
        self._source_search_edit = LineEdit(self)
        self._source_search_edit.setPlaceholderText(
            tr(
                "Filter author / username...",
                "筛选作者名 / username...",
                "作者名 / ユーザー名で絞り込み...",
            )
        )
        self._source_search_edit.setClearButtonEnabled(True)
        self._source_search_edit.setMinimumWidth(240)
        self._source_search_edit.textChanged.connect(self._apply_source_filters)
        source_meta_row.addWidget(self._source_search_edit)

        source_sort_label = BodyLabel(tr("Sort by", "排序字段", "並び替え項目"), self)
        _style_inline_label(source_sort_label)
        source_meta_row.addWidget(source_sort_label)
        self._source_sort_combo = ComboBox(self)
        for field in _SOURCE_SORT_OPTIONS:
            self._source_sort_combo.addItem(_source_sort_label(field))
            self._source_sort_combo.setItemData(self._source_sort_combo.count() - 1, field)
        saved_sort_index = self._source_sort_combo.findData(self._source_sort_field)
        self._source_sort_combo.setCurrentIndex(max(0, saved_sort_index))
        self._source_sort_combo.setFixedWidth(142)
        self._source_sort_combo.currentIndexChanged.connect(self._on_source_sort_changed)
        source_meta_row.addWidget(self._source_sort_combo)

        self._source_sort_direction_btn = ToolButton(self)
        self._source_sort_direction_btn.setFixedSize(36, _CONTROL_HEIGHT)
        self._source_sort_direction_btn.clicked.connect(self._toggle_source_sort_direction)
        source_meta_row.addWidget(self._source_sort_direction_btn)
        self._update_source_sort_direction_button()

        source_columns_btn = PrimaryPushButton(tr("Source Fields", "源字段", "購読元列"), self, FluentIcon.SETTING)
        _style_action_button(source_columns_btn, min_width=96)
        source_columns_btn.clicked.connect(self._configure_source_columns)
        source_meta_row.addWidget(source_columns_btn)
        left_layout.addLayout(source_meta_row)

        self._source_table = TableWidget(self)
        self._source_table.setColumnCount(12)
        self._source_table.setHorizontalHeaderLabels(
            [
                tr("Avatar", "头像", "アイコン"),
                tr("Subscription source", "订阅来源", "購読元"),
                tr("Type", "类型", "種類"),
                tr("Display Name", "名称（作者名）", "表示名"),
                tr("New", "新增", "新規"),
                tr("Missing", "未下载", "未保存"),
                tr("Items", "项目", "項目"),
                tr("Last Check", "上次刷新", "最終確認"),
                tr("Imported", "导入时间", "取込日時"),
                tr("Username", "名称（username）", "ユーザー名"),
                tr("Source URL", "来源URL", "元URL"),
                tr("Page", "主页", "ページ"),
            ]
        )
        self._source_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._source_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._source_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._source_table.setAlternatingRowColors(True)
        self._source_table.setBorderVisible(True)
        self._source_table.setBorderRadius(8)
        self._source_table.verticalHeader().setVisible(False)
        self._source_table.verticalHeader().setDefaultSectionSize(76)
        self._source_table.setIconSize(QSize(58, 58))
        self._source_table.currentCellChanged.connect(lambda *_args: self._load_items(self._selected_source_id()))
        self._source_table.cellClicked.connect(self._on_source_cell_clicked)
        self._source_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._source_table.customContextMenuRequested.connect(
            self._show_source_table_context_menu
        )
        source_header = self._source_table.horizontalHeader()
        source_header.setHighlightSections(False)
        source_header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        source_widths = {
            self._SRC_AVATAR: 76,
            self._SRC_STATE: 142,
            self._SRC_TYPE: 70,
            self._SRC_TITLE: 220,
            self._SRC_NEW: 52,
            self._SRC_UNDOWNLOADED: 72,
            self._SRC_ITEMS: 58,
            self._SRC_CHECKED: 150,
            self._SRC_CREATED: 150,
            self._SRC_KEY: 170,
            self._SRC_URL: 220,
            self._SRC_OPEN: 68,
        }
        restore_table_widths(self._source_table, "subscription_source_widths_v5", source_widths)
        connect_table_width_saver(self._source_table, "subscription_source_widths_v5")
        restore_table_columns(
            self._source_table,
            "subscription_source_table_v5",
            default_visible=[
                self._SRC_AVATAR,
                self._SRC_STATE,
                self._SRC_TITLE,
                self._SRC_KEY,
                self._SRC_NEW,
                self._SRC_UNDOWNLOADED,
                self._SRC_ITEMS,
                self._SRC_CREATED,
                self._SRC_URL,
            ],
        )
        connect_table_column_saver(self._source_table, "subscription_source_table_v5")
        source_header.sectionResized.connect(lambda *_args: self._fit_source_table_last_column())
        source_header.sectionMoved.connect(lambda *_args: self._fit_source_table_last_column())
        left_layout.addWidget(self._source_table, stretch=1)
        QTimer.singleShot(0, self._fit_source_table_last_column)

        item_summary_row = QHBoxLayout()
        item_summary_row.setSpacing(8)
        item_summary_row.addWidget(SubtitleLabel(tr("Videos", "视频列表", "動画一覧"), self))
        self._summary_label = BodyLabel("", self)
        self._summary_label.setWordWrap(True)
        self._summary_label.setMinimumHeight(_CONTROL_HEIGHT)
        self._summary_label.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self._summary_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        item_summary_row.addWidget(self._summary_label, stretch=1)
        item_controls_layout.addLayout(item_summary_row)

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
        history_btn = PrimaryDropDownPushButton(tr("History Actions", "历史操作", "履歴操作"), self, FluentIcon.HISTORY)
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
        self._refresh_current_btn = PrimaryDropDownPushButton(
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

        title_filter_row = ResponsiveFlowLayout()
        title_filter_row.setSpacing(_ROW_SPACING)
        title_filter_label = BodyLabel(tr("Title Filter", "标题筛选", "タイトルフィルター"), self)
        _style_inline_label(title_filter_label)
        title_filter_row.addWidget(title_filter_label)
        self._title_filter_mode_btn = PrimaryPushButton(tr("Simple", "简单搜索", "簡易検索"), self)
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

        self._item_grid_columns_label = BodyLabel(
            tr("Columns", "每行列数", "1行の列数"), self
        )
        _style_inline_label(self._item_grid_columns_label)
        item_filter_row.addWidget(self._item_grid_columns_label)
        self._item_grid_columns_combo = ComboBox(self)
        for columns in range(1, _MAX_SUBSCRIPTION_GRID_COLUMNS + 1):
            self._item_grid_columns_combo.addItem(str(columns))
            self._item_grid_columns_combo.setItemData(
                self._item_grid_columns_combo.count() - 1,
                str(columns),
            )
        try:
            saved_grid_columns = int(
                app_config.get_ui_value(
                    "subscription_grid_columns_v1",
                    _DEFAULT_SUBSCRIPTION_GRID_COLUMNS,
                )
                or _DEFAULT_SUBSCRIPTION_GRID_COLUMNS
            )
        except (TypeError, ValueError):
            saved_grid_columns = _DEFAULT_SUBSCRIPTION_GRID_COLUMNS
        self._item_grid_columns_combo.setCurrentIndex(
            max(1, min(_MAX_SUBSCRIPTION_GRID_COLUMNS, saved_grid_columns)) - 1
        )
        self._item_grid_columns_combo.setFixedWidth(84)
        self._item_grid_columns_combo.currentIndexChanged.connect(
            self._on_item_grid_columns_changed
        )
        item_filter_row.addWidget(self._item_grid_columns_combo)

        self._item_columns_btn = PrimaryPushButton(tr("Fields", "字段设置", "列設定"), self, FluentIcon.SETTING)
        _style_action_button(self._item_columns_btn, min_width=96)
        self._item_columns_btn.clicked.connect(self._configure_item_columns)
        item_filter_row.addWidget(self._item_columns_btn)
        item_controls_layout.addLayout(item_filter_row)

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

        self._item_stack = QStackedWidget(self)
        self._item_stack.addWidget(self._item_table)
        self._item_stack.addWidget(self._thumbnail_list)
        item_content_layout.addWidget(self._item_stack, stretch=1)
        _apply_fluent_scrollbars(self._item_table)
        _apply_fluent_scrollbars(self._thumbnail_list)
        restore_splitter_sizes(self._item_content_splitter, "subscription_item_content_splitter_sizes", [390, 1000])
        connect_splitter_saver(self._item_content_splitter, "subscription_item_content_splitter_sizes")
        self._update_selection_actions()
        self._sync_item_grid_controls()
        self._update_thumbnail_grid()
        fit_table_last_column(self._item_table)

    def refresh_theme_styles(self):
        """Refresh custom styles that qfluentwidgets cannot recolor automatically."""
        if not hasattr(self, "_item_table"):
            return
        _apply_fluent_scrollbars(self._item_table)
        _apply_fluent_scrollbars(self._thumbnail_list)
        _apply_fluent_scrollbars(self._item_controls_scroll)
        self._thumbnail_list.setStyleSheet(_thumbnail_list_style())
        _style_content_splitter(self._splitter)
        _style_content_splitter(self._item_content_splitter)
        self._sync_download_option_buttons()
        # Pending rows may have been staged before the theme changed. Reapply
        # only their brush so old and newly staged rows always share one green.
        self._set_pending_visuals(list(self._pending_video_ids), True)
        # Rebuild placeholders so unloaded covers also follow the selected theme.
        placeholder = self._thumbnail_placeholder_icon()
        for index in range(self._thumbnail_list.count()):
            item = self._thumbnail_list.item(index)
            video_id = str(item.data(Qt.ItemDataRole.UserRole) or "")
            data = next((entry for entry in self._visible_items if str(entry.get("video_id", "")) == video_id), None)
            path = str(data.get("thumbnail_path", "") or "") if data else ""
            if not path or not os.path.isfile(path):
                item.setIcon(placeholder)

    def _on_splitter_moved(self, *_args):
        sizes = list(self._splitter.sizes())
        if self._source_panel_visible and self._items_panel_visible and all(size > 0 for size in sizes):
            self._last_splitter_sizes = sizes
        self._fit_source_table_last_column()
        self._fit_item_table_last_column()
        self._update_thumbnail_grid()
        self._schedule_thumbnail_grid_update()

    def _update_panel_toggle_buttons(self):
        if not hasattr(self, "_toggle_sources_btn"):
            return
        source_hidden = not self._source_panel_visible
        items_hidden = not self._items_panel_visible
        vertical = bool(self._splitter_is_vertical)
        source_direction_icon = FluentIcon.UP if vertical else FluentIcon.LEFT_ARROW
        items_direction_icon = FluentIcon.DOWN if vertical else FluentIcon.RIGHT_ARROW
        self._toggle_sources_btn.setIcon(
            FluentIcon.VIEW if source_hidden else source_direction_icon
        )
        self._toggle_items_btn.setIcon(
            FluentIcon.VIEW if items_hidden else items_direction_icon
        )
        self._toggle_sources_btn.setToolTip(
            tr(
                (
                    "Show subscription sources"
                    if source_hidden
                    else "Hide the upper panel"
                    if vertical
                    else "Hide subscription sources"
                ),
                (
                    "显示订阅源"
                    if source_hidden
                    else "隐藏上方区域"
                    if vertical
                    else "隐藏订阅源"
                ),
                (
                    "購読元を表示"
                    if source_hidden
                    else "上側の領域を隠す"
                    if vertical
                    else "購読元を隠す"
                ),
            )
        )
        self._toggle_items_btn.setToolTip(
            tr(
                (
                    "Show video list"
                    if items_hidden
                    else "Hide the lower panel"
                    if vertical
                    else "Hide video list"
                ),
                (
                    "显示视频列表"
                    if items_hidden
                    else "隐藏下方区域"
                    if vertical
                    else "隐藏视频列表"
                ),
                (
                    "動画一覧を表示"
                    if items_hidden
                    else "下側の領域を隠す"
                    if vertical
                    else "動画一覧を隠す"
                ),
            )
        )

    def _set_panel_visible(self, index: int, visible: bool):
        if index == 0:
            if not visible and not self._items_panel_visible:
                return
            self._source_panel_visible = visible
            self._source_panel.setVisible(visible)
        else:
            if not visible and not self._source_panel_visible:
                return
            self._items_panel_visible = visible
            self._items_panel.setVisible(visible)

        if self._source_panel_visible and self._items_panel_visible:
            sizes = list(self._last_splitter_sizes)
            total = max(2, self._splitter.height() if self._splitter_is_vertical else self._splitter.width())
            if len(sizes) != 2 or sum(sizes) <= 0 or min(sizes) <= 0:
                sizes = [int(total * 0.4), int(total * 0.6)]
            self._splitter.setSizes(sizes)
        else:
            visible_index = 0 if self._source_panel_visible else 1
            visible_panel = self._source_panel if visible_index == 0 else self._items_panel
            visible_panel.setVisible(True)
            total = max(1, sum(self._splitter.sizes()))
            sizes = [0, 0]
            sizes[visible_index] = total
            self._splitter.setSizes(sizes)
        self._update_panel_toggle_buttons()
        self._fit_source_table_last_column()
        self._fit_item_table_last_column()
        self._update_thumbnail_grid()
        self._schedule_thumbnail_grid_update()

    def _toggle_source_panel(self):
        self._set_panel_visible(0, not self._source_panel_visible)

    def _toggle_item_panel(self):
        self._set_panel_visible(1, not self._items_panel_visible)

    def _fit_source_table_last_column(self):
        if not hasattr(self, "_source_table"):
            return
        header = self._source_table.horizontalHeader()
        visible_columns = [
            column for column in range(self._source_table.columnCount())
            if not self._source_table.isColumnHidden(column)
        ]
        if not visible_columns:
            return
        visible_columns.sort(key=header.visualIndex)
        last_column = visible_columns[-1]
        for column in visible_columns:
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
        # Keep the final visible field bound to the panel width. The splitter
        # therefore gives its extra space to the last source-table column.
        header.setSectionResizeMode(last_column, QHeaderView.ResizeMode.Stretch)

    def _fit_item_table_last_column(self):
        if hasattr(self, "_item_table"):
            fit_table_last_column(self._item_table)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Horizontal splitters are useful on wide screens, but at compact widths
        # they force both panels to keep their full toolbar width. Stack panels
        # vertically instead so every control can wrap naturally.
        should_stack = self.width() < 1100
        if should_stack != self._splitter_is_vertical:
            self._splitter_is_vertical = should_stack
            self._splitter.setOrientation(
                Qt.Orientation.Vertical if should_stack else Qt.Orientation.Horizontal
            )
            if self._source_panel_visible and self._items_panel_visible:
                if should_stack:
                    height = max(1, self._splitter.height())
                    self._splitter.setSizes([max(250, int(height * 0.50)), max(300, int(height * 0.50))])
                else:
                    width = max(1, self._splitter.width())
                    self._splitter.setSizes([max(420, int(width * 0.50)), max(520, int(width * 0.50))])
            else:
                visible_index = 0 if self._source_panel_visible else 1
                total = max(1, self._splitter.height() if should_stack else self._splitter.width())
                sizes = [0, 0]
                sizes[visible_index] = total
                self._splitter.setSizes(sizes)
        self._fit_source_table_last_column()
        self._fit_item_table_last_column()
        self._update_thumbnail_grid()
        self._schedule_thumbnail_grid_update()
        self._update_panel_toggle_buttons()

    def _schedule_thumbnail_grid_update(self):
        """Run one more layout pass after Qt has committed the new viewport size."""

        if self._thumbnail_grid_resize_pending:
            return
        self._thumbnail_grid_resize_pending = True
        QTimer.singleShot(0, self._run_scheduled_thumbnail_grid_update)

    def _run_scheduled_thumbnail_grid_update(self):
        self._thumbnail_grid_resize_pending = False
        self._update_thumbnail_grid()

    def _update_thumbnail_grid(self):
        if not hasattr(self, "_thumbnail_list"):
            return
        viewport_width = self._thumbnail_list.viewport().width()
        if viewport_width <= 0:
            return
        spacing = 8
        scrollbar_reserve = max(
            8,
            int(self._thumbnail_list.verticalScrollBar().sizeHint().width()) - 1,
        )
        available_width = max(1, viewport_width - scrollbar_reserve)
        try:
            requested_columns = int(
                self._item_grid_columns_combo.currentData()
                or _DEFAULT_SUBSCRIPTION_GRID_COLUMNS
            )
        except (TypeError, ValueError):
            requested_columns = _DEFAULT_SUBSCRIPTION_GRID_COLUMNS
        # The selector is an explicit user preference.  Preserve the exact
        # number of columns and shrink each card on compact split panes rather
        # than silently reducing (for example) 8 columns to 4.
        columns = max(1, min(_MAX_SUBSCRIPTION_GRID_COLUMNS, requested_columns))
        card_width = max(
            40,
            (available_width - spacing * (columns - 1)) // columns,
        )
        # The cover is the visual body of a card.  Match it to the item cell so
        # every column shares one exact left edge; the 1px allowance is for the
        # selection border rather than an arbitrary visual inset.
        image_width = max(32, card_width - 2)
        image_height = max(32, round(image_width * 9 / 16))
        text_height = _grid_text_height(
            self._thumbnail_list,
            max(40, card_width - 2),
            fallback_lines=4,
        )
        grid_size = QSize(card_width, image_height + text_height + 14)
        updates_enabled = self._thumbnail_list.updatesEnabled()
        self._thumbnail_list.setUpdatesEnabled(False)
        try:
            self._thumbnail_list.setIconSize(QSize(image_width, image_height))
            self._thumbnail_list.setGridSize(grid_size)
            self._thumbnail_list.setSpacing(spacing)
            for index in range(self._thumbnail_list.count()):
                self._thumbnail_list.item(index).setSizeHint(grid_size)
            # QListWidget may defer IconMode placement until the next paint.
            # Force it now so a column-count change never shows a partial card.
            self._thumbnail_list.doItemsLayout()
        finally:
            self._thumbnail_list.setUpdatesEnabled(updates_enabled)
        if updates_enabled:
            self._thumbnail_list.viewport().update()

    def _configure_source_columns(self):
        open_table_column_dialog(
            self._source_table,
            "subscription_source_table_v5",
            title=tr("Source Columns", "订阅源字段", "購読元列設定"),
            default_visible=[
                self._SRC_AVATAR,
                self._SRC_STATE,
                self._SRC_TITLE,
                self._SRC_KEY,
                self._SRC_NEW,
                self._SRC_UNDOWNLOADED,
                self._SRC_ITEMS,
                self._SRC_CREATED,
                self._SRC_URL,
            ],
            parent=self,
        )
        self._fit_source_table_last_column()

    def _configure_item_columns(self):
        open_table_column_dialog(
            self._item_table,
            "subscription_item_table_v2",
            title=tr("Video Columns", "作品列表字段", "動画列設定"),
            parent=self,
        )
        self._fit_item_table_last_column()

    def _on_source_sort_changed(self, index: int):
        if not hasattr(self, "_source_sort_combo"):
            return
        field = self._source_sort_combo.itemData(index)
        field = str(field or "title").strip().casefold()
        if field not in _SOURCE_SORT_FIELDS:
            field = "title"
        self._source_sort_field = field
        app_config.set_ui_value("subscription_source_sort_field_v1", field)
        self._apply_source_filters()

    def _toggle_source_sort_direction(self):
        self._source_sort_desc = not self._source_sort_desc
        app_config.set_ui_value(
            "subscription_source_sort_desc_v1",
            "1" if self._source_sort_desc else "0",
        )
        self._update_source_sort_direction_button()
        self._apply_source_filters()

    def _update_source_sort_direction_button(self):
        button = getattr(self, "_source_sort_direction_btn", None)
        if button is None:
            return
        button.setIcon(FluentIcon.DOWN if self._source_sort_desc else FluentIcon.UP)
        button.setToolTip(
            tr(
                "Descending order" if self._source_sort_desc else "Ascending order",
                "倒序排列" if self._source_sort_desc else "正序排列",
                "降順" if self._source_sort_desc else "昇順",
            )
        )

    def _load_sources(self):
        previous_source_id = self._selected_source_id()
        self._all_sources = download_manager.get_subscription_sources()
        self._apply_source_filters(preferred_source_id=previous_source_id)

    def _refresh_sources_keep_current_items(self):
        source_id = self._current_source_id
        self._all_sources = download_manager.get_subscription_sources()
        self._apply_source_filters(preferred_source_id=source_id)

    def _apply_source_filters(self, *_args, preferred_source_id: int | None = None):
        selected_source_id = preferred_source_id if preferred_source_id is not None else self._selected_source_id()
        query = self._source_search_edit.text().strip().casefold() if hasattr(self, "_source_search_edit") else ""
        sources = sorted(
            self._all_sources,
            key=lambda source: _source_sort_key(
                source,
                getattr(self, "_source_sort_field", "title"),
            ),
            reverse=bool(getattr(self, "_source_sort_desc", False)),
        )
        if query:
            sources = [source for source in sources if query in _source_search_text(source)]
        self._sources = sources
        self._render_sources()
        if selected_source_id is not None and self._select_source_id(selected_source_id):
            self._load_items(selected_source_id)
            return
        if self._sources:
            first_source_id = int(self._sources[0].get("id", 0) or 0)
            self._source_table.blockSignals(True)
            self._source_table.clearSelection()
            self._source_table.selectRow(0)
            self._source_table.setCurrentCell(0, self._SRC_STATE)
            self._source_table.blockSignals(False)
            self._load_items(first_source_id or None)
            return
        self._load_items(None)

    def _select_source_id(self, source_id: int) -> bool:
        selected_row = -1
        for row, source in enumerate(self._sources):
            if int(source.get("id", 0) or 0) == int(source_id):
                selected_row = row
                break
        if selected_row < 0:
            return False
        self._source_table.blockSignals(True)
        self._source_table.clearSelection()
        self._source_table.selectRow(selected_row)
        self._source_table.setCurrentCell(selected_row, self._SRC_STATE)
        self._source_table.blockSignals(False)
        return True

    def _render_sources(self):
        self._source_render_timer.stop()
        self._source_render_index = 0
        self._source_table.blockSignals(True)
        self._source_table.setUpdatesEnabled(False)
        self._source_table.clearContents()
        self._source_table.setRowCount(len(self._sources))
        self._source_table.setUpdatesEnabled(True)
        self._source_table.blockSignals(False)
        self._source_render_timer.start()
        self._start_avatar_worker_for_missing_sources()

    def _render_source_batch(self):
        start = self._source_render_index
        if start >= len(self._sources):
            self._source_render_timer.stop()
            return
        end = min(start + self._RENDER_BATCH_SIZE, len(self._sources))
        self._source_table.setUpdatesEnabled(False)
        try:
            for row in range(start, end):
                self._render_source_row(row, self._sources[row])
        finally:
            self._source_table.setUpdatesEnabled(True)
        self._source_render_index = end
        if end >= len(self._sources):
            self._source_render_timer.stop()

    def _render_source_row(self, row: int, source: dict[str, Any]):
        source_id = int(source.get("id", 0) or 0)
        source_url = _source_url(source)
        origin_label = _source_origin_label(source)
        avatar_item = self._make_source_avatar_item(source_id, source)
        self._source_table.setItem(row, self._SRC_AVATAR, avatar_item)
        values = {
            self._SRC_STATE: origin_label,
            self._SRC_TYPE: _source_type_label(str(source.get("source_type", "") or "")),
            self._SRC_TITLE: str(source.get("title", "") or ""),
            self._SRC_NEW: str(source.get("new_count", 0) or 0),
            self._SRC_UNDOWNLOADED: str(source.get("undownloaded_count", 0) or 0),
            self._SRC_ITEMS: str(source.get("item_count", 0) or 0),
            self._SRC_CHECKED: str(source.get("last_checked_at", "") or ""),
            self._SRC_CREATED: str(source.get("created_at", "") or ""),
            self._SRC_KEY: str(source.get("source_key", "") or ""),
        }
        for col, value in values.items():
            item = QTableWidgetItem(value)
            item.setData(Qt.ItemDataRole.UserRole, source_id)
            item.setToolTip(value)
            if col == self._SRC_STATE:
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item.setToolTip(origin_label)
                item.setForeground(QColor(_source_origin_color(source)))
            if col in (self._SRC_NEW, self._SRC_UNDOWNLOADED, self._SRC_ITEMS):
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._source_table.setItem(row, col, item)
        source_url_item = QTableWidgetItem(source_url)
        source_url_item.setData(Qt.ItemDataRole.UserRole, source_id)
        source_url_item.setToolTip(source_url or tr("No source URL", "没有订阅源链接", "購読元URLがありません"))
        self._source_table.setItem(row, self._SRC_URL, source_url_item)
        self._set_action_item(
            self._source_table,
            row,
            self._SRC_OPEN,
            source_id,
            "open_source",
            tr("Open", "打开", "開く"),
            tr("Open source page", "打开订阅源页面", "購読元ページを開く"),
            bool(source_url),
            action_url=source_url,
        )

    def _make_source_avatar_item(self, source_id: int, source: dict[str, Any]) -> QTableWidgetItem:
        item = QTableWidgetItem("")
        item.setData(Qt.ItemDataRole.UserRole, source_id)
        title = str(source.get("title", "") or source.get("source_key", "") or "")
        avatar_path = str(source.get("avatar_path", "") or "")
        if avatar_path and os.path.isfile(avatar_path):
            pixmap = QPixmap(avatar_path)
            if not pixmap.isNull():
                size = self._source_table.iconSize()
                scaled = pixmap.scaled(
                    size,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                item.setIcon(QIcon(scaled))
                item.setToolTip(title)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                return item
        item.setText((title[:1] or "?").upper())
        font = item.font()
        font.setPointSize(max(14, font.pointSize() + 4))
        item.setFont(font)
        item.setToolTip(title or tr("No avatar cached yet", "头像尚未缓存", "アバター未保存"))
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        item.setForeground(QColor("#777777"))
        return item

    def _start_avatar_worker_for_missing_sources(self):
        if self._shutting_down:
            return
        if self._avatar_worker and self._avatar_worker.isRunning():
            return
        source_ids: list[int] = []
        for source in self._sources:
            if str(source.get("source_type", "") or "") != "author":
                continue
            source_id = int(source.get("id", 0) or 0)
            if not source_id or source_id in self._avatar_requested_source_ids:
                continue
            avatar_path = str(source.get("avatar_path", "") or "")
            if avatar_path and os.path.isfile(avatar_path):
                continue
            source_ids.append(source_id)
        if not source_ids:
            return
        self._avatar_requested_source_ids.update(source_ids)
        self._avatar_worker = SubscriptionAvatarWorker(source_ids)
        self._avatar_worker.avatar_ready.connect(self._on_avatar_ready)
        self._avatar_worker.done.connect(self._on_avatar_worker_finished)
        self._avatar_worker.start()

    def _on_avatar_ready(self, source_id: int, avatar_url: str, avatar_path: str):
        for collection in (self._all_sources, self._sources):
            for source in collection:
                if int(source.get("id", 0) or 0) == int(source_id):
                    source["avatar_url"] = avatar_url
                    source["avatar_path"] = avatar_path
        for row, source in enumerate(self._sources):
            if int(source.get("id", 0) or 0) == int(source_id):
                self._render_source_row(row, source)
                break

    def _on_avatar_worker_finished(self):
        self._avatar_worker = None
        self._start_avatar_worker_for_missing_sources()

    def _load_items(self, source_id: int | None):
        self._current_source_id = source_id
        self._all_items = download_manager.get_subscription_items(source_id)
        self._apply_item_filters()

    def _on_title_filter_mode_toggled(self, regex_mode: bool):
        self._title_filter_mode_btn.setText(
            tr("Regex", "正则模式", "正規表現")
            if regex_mode else tr("Simple", "简单搜索", "簡易検索")
        )
        self._title_filter_mode_btn.setToolTip(
            tr("Switch to simple search", "切换到简单搜索", "簡易検索に切替")
            if regex_mode else tr("Switch to regex mode", "切换到正则模式", "正規表現モードに切替")
        )
        if hasattr(self, "_title_search_edit"):
            self._title_search_edit.setPlaceholderText(
                tr(
                    "Search titles as you type…",
                    "输入标题关键词，实时筛选…",
                    "タイトルを入力して絞り込み…",
                )
                if not regex_mode else tr(
                    "Python regex, e.g. \\b\\d{2}-\\d{2}\\b",
                    "输入 Python 正则，例如 \\b\\d{2}-\\d{2}\\b",
                    "Python 正規表現。例: \\b\\d{2}-\\d{2}\\b",
                )
            )
        self._apply_item_filters()

    def _apply_item_filters(self, *_args):
        self._sync_items_with_current_tasks()
        items = list(self._all_items)
        install_idx = self._install_filter_combo.currentIndex() if hasattr(self, "_install_filter_combo") else 0
        new_idx = self._new_filter_combo.currentIndex() if hasattr(self, "_new_filter_combo") else 0
        sort_idx = self._item_sort_combo.currentIndex() if hasattr(self, "_item_sort_combo") else 0
        title_query = self._title_search_edit.text().strip() if hasattr(self, "_title_search_edit") else ""
        title_mode = 1 if getattr(self, "_title_filter_mode_btn", None) and self._title_filter_mode_btn.isChecked() else 0
        self._title_filter_error = ""
        if title_query:
            try:
                matcher = _title_matcher(title_query, regex_mode=title_mode == 1)
            except re.error as exc:
                self._title_filter_error = str(exc)
                items = []
            else:
                items = [
                    item
                    for item in items
                    if matcher(str(item.get("title", "") or item.get("video_id", "") or ""))
                ]

        def is_downloaded(item: dict[str, Any]) -> bool:
            return bool(item.get("downloaded"))

        def file_exists(item: dict[str, Any]) -> bool:
            return bool(item.get("download_file_exists"))

        def unavailable(item: dict[str, Any]) -> bool:
            return _item_not_downloadable(item)

        if install_idx == 1:
            items = [item for item in items if not is_downloaded(item) and not item.get("queued") and not unavailable(item)]
        elif install_idx == 2:
            items = [item for item in items if unavailable(item)]
        elif install_idx == 3:
            items = [item for item in items if is_downloaded(item) and file_exists(item)]
        elif install_idx == 4:
            items = [item for item in items if is_downloaded(item) and not file_exists(item)]
        elif install_idx == 5:
            items = [item for item in items if item.get("queued")]

        if new_idx == 1:
            items = [item for item in items if int(item.get("is_new", 0) or 0)]
        elif new_idx == 2:
            items = [item for item in items if not int(item.get("is_new", 0) or 0)]

        if sort_idx == 1:
            items.sort(key=_item_date_key)
        elif sort_idx == 2:
            items.sort(key=_item_date_key, reverse=True)
            items.sort(key=lambda item: 0 if int(item.get("is_new", 0) or 0) else 1)
        elif sort_idx == 3:
            items.sort(key=lambda item: str(item.get("title", "") or item.get("video_id", "") or "").lower())
        else:
            items.sort(key=_item_date_key, reverse=True)

        self._visible_items = items
        self._render_items()

    def _render_items(self):
        self._item_render_timer.stop()
        self._item_render_index = 0
        new_count = downloaded_count = moved_count = queued_count = unavailable_count = 0
        for item_data in self._all_items:
            downloaded = bool(item_data.get("downloaded"))
            file_exists = bool(item_data.get("download_file_exists"))
            queued = bool(item_data.get("queued"))
            is_new = bool(int(item_data.get("is_new", 0) or 0))
            if is_new:
                new_count += 1
            if downloaded and file_exists:
                downloaded_count += 1
            if downloaded and not file_exists:
                moved_count += 1
            if queued:
                queued_count += 1
            if _item_not_downloadable(item_data):
                unavailable_count += 1
        self._summary_label.setText(
            tr(
                f"Visible: {len(self._visible_items)}/{len(self._all_items)} | pending: {len(self._pending_video_ids)} | new: {new_count} | downloaded: {downloaded_count} | moved: {moved_count} | queued: {queued_count} | unavailable: {unavailable_count}",
                f"当前显示: {len(self._visible_items)}/{len(self._all_items)} | 待操作: {len(self._pending_video_ids)} | 新增: {new_count} | 本地已下载: {downloaded_count} | 已移走: {moved_count} | 已在队列: {queued_count} | 不可下载: {unavailable_count}",
                f"表示: {len(self._visible_items)}/{len(self._all_items)} | 操作待ち: {len(self._pending_video_ids)} | 新規: {new_count} | 保存済み: {downloaded_count} | 移動済み: {moved_count} | キュー内: {queued_count} | 保存不可: {unavailable_count}",
            )
        )
        if self._title_filter_error:
            self._title_search_edit.setToolTip(
                tr(
                    f"Invalid regular expression: {self._title_filter_error}",
                    f"正则表达式无效：{self._title_filter_error}",
                    f"正規表現が無効です：{self._title_filter_error}",
                )
            )
            self._summary_label.setToolTip(
                tr(
                    f"Invalid regular expression: {self._title_filter_error}",
                    f"正则表达式无效：{self._title_filter_error}",
                    f"正規表現が無効です：{self._title_filter_error}",
                )
            )
        else:
            self._title_search_edit.setToolTip("")
            self._summary_label.setToolTip("")

        cover_mode = (
            hasattr(self, "_item_view_combo")
            and self._item_view_combo.currentIndex() == 1
        )
        if hasattr(self, "_item_stack"):
            self._item_stack.setCurrentIndex(1 if cover_mode else 0)
        if cover_mode:
            self._item_table.clearContents()
            self._item_table.setRowCount(0)
            self._render_thumbnail_items()
        else:
            self._thumbnail_list.clear()
            self._thumbnail_items_by_video_id.clear()
            self._item_table.setUpdatesEnabled(False)
            self._item_table.clearContents()
            self._item_table.setRowCount(len(self._visible_items))
            self._item_table.setUpdatesEnabled(True)
            self._item_render_timer.start()
        self._update_selection_actions()

    def _on_item_view_changed(self, _index: int):
        self._sync_item_grid_controls()
        self._render_items()
        QTimer.singleShot(0, self._update_thumbnail_grid)

    def _sync_item_grid_controls(self):
        cover_mode = (
            hasattr(self, "_item_view_combo")
            and self._item_view_combo.currentIndex() == 1
        )
        if hasattr(self, "_item_grid_columns_label"):
            self._item_grid_columns_label.setVisible(cover_mode)
        if hasattr(self, "_item_grid_columns_combo"):
            self._item_grid_columns_combo.setVisible(cover_mode)
        if hasattr(self, "_item_columns_btn"):
            self._item_columns_btn.setVisible(not cover_mode)

    def _on_item_grid_columns_changed(self, _index: int):
        if not hasattr(self, "_item_grid_columns_combo"):
            return
        value = str(
            self._item_grid_columns_combo.currentData()
            or _DEFAULT_SUBSCRIPTION_GRID_COLUMNS
        )
        app_config.set_ui_value("subscription_grid_columns_v1", value)
        self._update_thumbnail_grid()
        self._schedule_thumbnail_grid_update()

    def _render_thumbnail_items(self):
        self._update_thumbnail_grid()
        self._thumbnail_list.setUpdatesEnabled(False)
        self._thumbnail_list.clear()
        self._thumbnail_items_by_video_id.clear()
        placeholder = self._thumbnail_placeholder_icon()
        try:
            for item_data in self._visible_items:
                video_id = str(item_data.get("video_id", "") or "").strip()
                if not video_id:
                    continue
                title = str(item_data.get("title", "") or video_id)
                author = str(item_data.get("author", "") or item_data.get("source_title", "") or "")
                published = _date_only(str(item_data.get("published_at", "") or ""))
                state = _item_state_text(
                    downloaded=bool(item_data.get("downloaded")),
                    queued=bool(item_data.get("queued")),
                    file_exists=bool(item_data.get("download_file_exists")),
                    task_status=str(item_data.get("task_status", "") or ""),
                    download_state=str(item_data.get("download_state", "") or ""),
                    download_reason=str(item_data.get("download_reason", "") or ""),
                )
                label = _ellipsize(title, 56)
                if author:
                    label += f"\n{_ellipsize(author, 44)}"
                if published:
                    label += f"\n{published}"
                label += f"\n{state}"
                path = str(item_data.get("thumbnail_path", "") or "")
                icon = self._thumbnail_icon(path) if path and os.path.isfile(path) else placeholder
                list_item = QListWidgetItem(icon, label)
                list_item.setData(Qt.ItemDataRole.UserRole, video_id)
                list_item.setToolTip(
                    tr(
                        f"{title}\nAuthor: {author}\nDouble-click to open",
                        f"{title}\n作者: {author}\n双击后按设置打开",
                        f"{title}\n作者: {author}\nダブルクリックで開く",
                    )
                )
                list_item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
                self._thumbnail_list.addItem(list_item)
                if video_id in self._pending_video_ids:
                    list_item.setBackground(self._pending_item_brush())
                self._thumbnail_items_by_video_id.setdefault(video_id, []).append(list_item)
        finally:
            self._thumbnail_list.setUpdatesEnabled(True)
        # Recalculate after the real captions are present; long titles may
        # wrap to an extra line and should determine the row height.
        self._update_thumbnail_grid()
        self._schedule_thumbnail_grid_update()
        self._start_thumbnail_worker_for_visible_items()

    def _thumbnail_placeholder_icon(self) -> QIcon:
        pixmap = QPixmap(self._thumbnail_list.iconSize())
        pixmap.fill(QColor("#34373d" if isDarkTheme() else "#e8e8e8"))
        return QIcon(pixmap)

    def _thumbnail_icon(self, path: str) -> QIcon:
        pixmap = QPixmap(path)
        if pixmap.isNull():
            return self._thumbnail_placeholder_icon()
        size = self._thumbnail_list.iconSize()
        scaled = pixmap.scaled(
            size,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        x = max(0, (scaled.width() - size.width()) // 2)
        y = max(0, (scaled.height() - size.height()) // 2)
        return QIcon(scaled.copy(x, y, size.width(), size.height()))

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

    def _start_thumbnail_worker_for_visible_items(self, *, force: bool = False) -> bool:
        if self._shutting_down:
            return False
        if self._thumbnail_worker and self._thumbnail_worker.isRunning():
            if force:
                self._thumbnail_force_refresh_pending = True
            return True
        requests: list[tuple[str, str]] = []
        for item_data in self._visible_items:
            video_id = str(item_data.get("video_id", "") or "").strip()
            thumbnail_url = str(item_data.get("thumbnail_url", "") or "").strip()
            thumbnail_path = str(item_data.get("thumbnail_path", "") or "").strip()
            if (
                not video_id
                or (not force and thumbnail_path and os.path.isfile(thumbnail_path))
                or (not force and video_id in self._thumbnail_requested_video_ids)
                or (force and video_id in self._thumbnail_force_refresh_ids)
                or (not force and not thumbnail_url)
            ):
                continue
            requests.append((video_id, thumbnail_url))
            if len(requests) >= 80:
                break
        if not requests:
            if force:
                self._thumbnail_force_refresh_ids.clear()
            return False
        self._thumbnail_requested_video_ids.update(video_id for video_id, _url in requests)
        if force:
            self._thumbnail_force_refresh_ids.update(video_id for video_id, _url in requests)
        self._thumbnail_worker = SubscriptionThumbnailWorker(
            requests,
            force=force,
            concurrency=self._cover_download_concurrency(),
        )
        self._thumbnail_worker.thumbnail_ready.connect(self._on_thumbnail_ready)
        self._thumbnail_worker.done.connect(self._on_thumbnail_worker_finished)
        self._thumbnail_worker.start()
        return True

    def _on_thumbnail_ready(self, video_id: str, path: str):
        for collection in (self._all_items, self._visible_items):
            for item_data in collection:
                if str(item_data.get("video_id", "") or "") == video_id:
                    item_data["thumbnail_path"] = path
        icon = self._thumbnail_icon(path)
        for list_item in self._thumbnail_items_by_video_id.get(video_id, []):
            list_item.setIcon(icon)

    def _on_thumbnail_worker_finished(self):
        worker = self._thumbnail_worker
        self._thumbnail_worker = None
        force_batch = bool(worker and getattr(worker, "_force", False))
        if worker:
            worker.deleteLater()
        force_refresh = self._thumbnail_force_refresh_pending
        self._thumbnail_force_refresh_pending = False
        if force_refresh or force_batch:
            if not self._start_thumbnail_worker_for_visible_items(force=True):
                self._thumbnail_force_refresh_ids.clear()
        elif (
            hasattr(self, "_item_view_combo")
            and self._item_view_combo.currentIndex() == 1
        ):
            self._start_thumbnail_worker_for_visible_items()

    def _render_item_batch(self):
        start = self._item_render_index
        if start >= len(self._visible_items):
            self._item_render_timer.stop()
            return
        end = min(start + self._RENDER_BATCH_SIZE, len(self._visible_items))
        self._item_table.setUpdatesEnabled(False)
        try:
            for row in range(start, end):
                self._render_item_row(row, self._visible_items[row])
        finally:
            self._item_table.setUpdatesEnabled(True)
        self._item_render_index = end
        if end >= len(self._visible_items):
            self._item_render_timer.stop()

    def _render_item_row(self, row: int, item_data: dict[str, Any]):
        video_id = str(item_data.get("video_id", "") or "")
        downloaded = bool(item_data.get("downloaded"))
        queued = bool(item_data.get("queued"))
        task_status = str(item_data.get("task_status", "") or "")
        download_reason = str(item_data.get("download_reason", "") or "")
        is_new = bool(int(item_data.get("is_new", 0) or 0))
        file_exists = bool(item_data.get("download_file_exists"))
        source_title = str(item_data.get("source_title", "") or "")
        source_url = str(item_data.get("source_url", "") or _video_url(video_id))
        state = _item_state_text(
            downloaded=downloaded,
            queued=queued,
            file_exists=file_exists,
            task_status=task_status,
            download_state=str(item_data.get("download_state", "") or ""),
            download_reason=download_reason,
        )
        values = [
            state,
            download_reason,
            tr("Yes", "是", "はい") if is_new else "",
            str(item_data.get("title", "") or video_id),
            str(item_data.get("author", "") or ""),
            _date_only(str(item_data.get("published_at", "") or "")),
            video_id,
        ]
        for col, value in enumerate(values):
            cell = QTableWidgetItem(value)
            cell.setData(Qt.ItemDataRole.UserRole, video_id)
            tooltip = value
            if col in (self._ITEM_TITLE, self._ITEM_AUTHOR):
                tooltip = tr(
                    f"{value}\nSource: {source_title}",
                    f"{value}\n来源: {source_title}",
                    f"{value}\n元: {source_title}",
                )
                if download_reason:
                    tooltip += f"\n{download_reason}"
            if col in (self._ITEM_STATE, self._ITEM_REASON) and download_reason:
                tooltip = download_reason
            cell.setToolTip(tooltip)
            if col in (self._ITEM_STATE, self._ITEM_NEW):
                cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if col == self._ITEM_STATE:
                cell.setForeground(
                    _state_color(
                        downloaded=downloaded,
                        queued=queued,
                        file_exists=file_exists,
                        task_status=task_status,
                        download_state=str(item_data.get("download_state", "") or ""),
                    )
                )
            elif col == self._ITEM_REASON and download_reason:
                cell.setForeground(QColor("#c42b1c"))
            elif col == self._ITEM_NEW and is_new:
                cell.setForeground(QColor("#c17d00"))
            self._item_table.setItem(row, col, cell)

        source_url_item = QTableWidgetItem(source_url)
        source_url_item.setData(Qt.ItemDataRole.UserRole, video_id)
        source_url_item.setToolTip(source_url or tr("No video URL", "没有视频链接", "動画URLがありません"))
        self._item_table.setItem(row, self._ITEM_SOURCE_URL, source_url_item)
        self._set_action_item(
            self._item_table,
            row,
            self._ITEM_URL,
            video_id,
            "open_url",
            tr("Page", "页面", "ページ"),
            tr("Open video page", "打开视频页", "動画ページを開く"),
            bool(video_id),
            action_url=_video_url(video_id),
        )
        self._set_action_item(
            self._item_table,
            row,
            self._ITEM_FOLDER,
            video_id,
            "open_folder",
            tr("Folder", "文件夹", "フォルダー"),
            tr("Open downloaded folder", "打开下载文件夹", "保存フォルダーを開く"),
            file_exists,
        )
        self._set_action_item(
            self._item_table,
            row,
            self._ITEM_FILE,
            video_id,
            "open_file",
            tr("File", "文件", "ファイル"),
            tr("Open downloaded file", "打开下载文件", "保存ファイルを開く"),
            file_exists,
        )

        if video_id in self._pending_video_ids:
            self._set_pending_row_background(row, True)

    def shutdown(self, *, timeout_ms: int = 30_000) -> bool:
        """Stop timers and subscription workers before the page is destroyed."""
        self._shutting_down = True
        self._source_render_timer.stop()
        self._item_render_timer.stop()
        workers: list[QThread | None] = [
            self._worker,
            self._import_worker,
            self._enqueue_worker,
            self._avatar_worker,
            self._thumbnail_worker,
        ]
        return stop_qthreads(workers, timeout_ms=timeout_ms)

    def _show_error(self, msg: str):
        InfoBar.error(
            title=tr("Operation Failed", "操作失败", "操作失敗"),
            content=msg,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=3500,
            parent=self,
        )

    def _on_task_status_changed(self, _task_id: str, _status_str: str):
        self._schedule_items_refresh_after_task_change()

    def _on_task_changed(self, *_args):
        self._schedule_items_refresh_after_task_change()

    def _on_tasks_changed(self, *_args):
        self._schedule_items_refresh_after_task_change()

    def _schedule_items_refresh_after_task_change(self):
        if self._items_refresh_pending:
            return
        self._items_refresh_pending = True
        QTimer.singleShot(150, self._refresh_visible_items_after_task_change)

    def _refresh_visible_items_after_task_change(self):
        self._items_refresh_pending = False
        self._load_items(self._current_source_id)

    def _sync_items_with_current_tasks(self):
        task_status_by_video_id = {
            task.video_id.lower(): task.status.value
            for task in download_manager.get_tasks()
            if task.video_id
        }
        for item in self._all_items:
            video_id = str(item.get("video_id", "") or "").lower()
            task_status = task_status_by_video_id.get(video_id, "") if video_id else ""
            item["task_status"] = task_status
            item["queued"] = bool(task_status)
