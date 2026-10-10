"""Build the source panel of the table view: actions, filter row and source table."""

from __future__ import annotations

import os

from PySide6.QtCore import QSize, QTimer, Qt
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QHBoxLayout, QVBoxLayout

from qfluentwidgets import (
    BodyLabel,
    ComboBox,
    FluentIcon,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    SubtitleLabel,
    TableWidget,
    ToolButton,
)

from ...i18n import tr
from .helpers import _source_sort_label
from .components import (
    _CONTROL_HEIGHT,
    _ROW_SPACING,
    _SOURCE_SORT_OPTIONS,
    _style_action_button,
    _style_inline_label,
)
from ..flow_layout import ResponsiveFlowLayout
from ..ui_state import (
    connect_table_column_saver,
    connect_table_width_saver,
    restore_table_columns,
    restore_table_widths,
)
from .page_dependencies import download_manager


class SubscriptionSourcePanelLayoutMixin:
    """Build the source panel of the table view: actions, filter row and source table."""

    def _build_source_actions(self, left_layout: QVBoxLayout):
        source_header = QHBoxLayout()
        source_header.setSpacing(8)
        source_header.addWidget(SubtitleLabel(tr("Subscriptions", "订阅源", "購読元"), self))
        source_header.addStretch()
        left_layout.addLayout(source_header)

        source_actions = ResponsiveFlowLayout()
        source_actions.setSpacing(_ROW_SPACING)
        import_authors_btn = PushButton(tr("Import Followed", "导入关注作者", "フォローを取込"), self, FluentIcon.PEOPLE)
        _style_action_button(import_authors_btn)
        import_authors_btn.clicked.connect(self._import_followed_authors)
        source_actions.addWidget(import_authors_btn)

        add_source_btn = PrimaryPushButton(tr("Add Author / Playlist", "添加作者/播放列表", "作者/リストを追加"), self, FluentIcon.PEOPLE)
        _style_action_button(add_source_btn)
        add_source_btn.clicked.connect(self._add_source)
        source_actions.addWidget(add_source_btn)

        delete_source_btn = PushButton(
            tr("Delete Subscription", "删除订阅", "購読を削除"),
            self,
            FluentIcon.DELETE,
        )
        _style_action_button(delete_source_btn)
        delete_source_btn.clicked.connect(self._delete_selected_source)
        source_actions.addWidget(delete_source_btn)

        self._refresh_all_btn = PushButton(tr("Refresh All", "刷新全部", "全件更新"), self, FluentIcon.SYNC)
        _style_action_button(self._refresh_all_btn)
        self._refresh_all_btn.clicked.connect(self._refresh_all)
        source_actions.addWidget(self._refresh_all_btn)
        left_layout.addLayout(source_actions)

    def _build_source_meta_row(self, left_layout: QVBoxLayout):
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

        source_columns_btn = ToolButton(FluentIcon.SETTING, self)
        source_columns_btn.setFixedSize(36, _CONTROL_HEIGHT)
        source_columns_btn.setToolTip(tr("Source Fields", "源字段", "購読元列"))
        source_columns_btn.clicked.connect(self._configure_source_columns)
        source_meta_row.addWidget(source_columns_btn)
        left_layout.addLayout(source_meta_row)

    def _build_source_table(self, left_layout: QVBoxLayout):
        self._source_table = TableWidget(self)
        self._source_table.setColumnCount(12)
        self._source_table.setHorizontalHeaderLabels(
            [
                tr("Avatar", "头像", "アイコン"),
                tr("Subscription source", "订阅来源", "購読元"),
                tr("Type", "类型", "種類"),
                tr("Display Name", "作者名", "表示名"),
                tr("New", "新增", "新規"),
                tr("Missing", "未下载", "未保存"),
                tr("Items", "项目", "項目"),
                tr("Last Check", "上次刷新", "最終確認"),
                tr("Imported", "导入时间", "取込日時"),
                tr("Username", "用户名", "ユーザー名"),
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
