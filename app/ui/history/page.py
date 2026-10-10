"""History Center Interface — DB-backed downloaded file browser."""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from qfluentwidgets import (
    CaptionLabel,
    ComboBox,
    FluentIcon,
    InfoBar,
    InfoBarPosition,
    PushButton,
    SearchLineEdit,
    TableWidget,
    TitleLabel,
    ToolButton,
)

from ...config import app_config
from ...core.manager import download_manager
from ...core.models import TaskStatus
from ...i18n import tr
from ...signal_bus import signal_bus
from ..theme import PAGE_MARGINS, PAGE_SPACING, FluentSplitter, set_secondary_text
from ..flow_layout import ResponsiveFlowLayout
from ..ui_state import (
    connect_table_column_saver,
    connect_table_width_saver,
    fit_table_last_column,
    restore_table_columns,
    restore_table_widths,
)
from .browse import HistoryBrowseMixin
from .csv_export import HistoryCsvExportMixin
from .record_actions import HistoryRecordActionsMixin
from .record_state import HistoryRecordStateMixin
from .selection import HistorySelectionMixin
from .sorting import HistorySortingMixin
from .table import HistoryTableMixin


class HistoryInterface(
    HistoryBrowseMixin,
    HistoryCsvExportMixin,
    HistoryRecordStateMixin,
    HistoryTableMixin,
    HistorySortingMixin,
    HistorySelectionMixin,
    HistoryRecordActionsMixin,
    QWidget,
):
    """Downloaded history page backed by data/history.db."""

    _COL_STATE = 0
    _COL_TITLE = 1
    _COL_AUTHOR = 2
    _COL_QUALITY = 3
    _COL_PUBLISHED = 4
    _COL_LIKES = 5
    _COL_VIEWS = 6
    _COL_DOWNLOADED = 7
    _COL_ID = 8
    _COL_PATH = 9
    _COL_SOURCE_URL = 10
    _COL_OPEN_URL = 11
    _COL_OPEN_FOLDER = 12
    _COL_OPEN_FILE = 13
    _COL_RENAME = 14
    _COL_REMOVE = 15

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("HistoryInterface")
        self._all_records: list[dict[str, Any]] = []
        self._records_by_id: dict[str, dict[str, Any]] = {}
        self._sort_column = self._COL_DOWNLOADED
        self._sort_reverse = True
        self._history_dirty = False
        self._history_refresh_timer = QTimer(self)
        self._history_refresh_timer.setSingleShot(True)
        self._history_refresh_timer.setInterval(120)
        self._history_refresh_timer.timeout.connect(self._load_history)
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.timeout.connect(self._continue_render)
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(180)
        self._filter_timer.timeout.connect(self._apply_filters)
        self._render_records: list[dict[str, Any]] = []
        self._render_next = 0
        self._render_deferred = False
        self._state_memo: dict[int, tuple[str, str, str, bool]] | None = None
        self._init_browse_state()

        self._build_ui()
        self._sync_browse_controls()
        self._load_history()
        signal_bus.task_status_changed.connect(self._on_task_status_changed)
        # Marked watched / downloaded elsewhere: re-read when next looked at.
        signal_bus.history_changed.connect(self._schedule_history_refresh)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(*PAGE_MARGINS)
        root.setSpacing(PAGE_SPACING)

        title_row = QHBoxLayout()
        title_row.addWidget(TitleLabel(tr("History Center", "历史记录中心", "履歴センター"), self))
        title_row.addStretch()
        refresh_btn = PushButton(tr("Refresh", "刷新", "更新"), self, FluentIcon.SYNC)
        refresh_btn.setToolTip(
            tr(
                "Reload DB and refresh file states; no records are deleted",
                "重新读取数据库并刷新文件状态；不会删除任何记录",
                "DBを再読み込みし状態を更新します。履歴は削除しません",
            )
        )
        refresh_btn.clicked.connect(self._load_history)
        title_row.addWidget(refresh_btn)
        root.addLayout(title_row)

        self._db_label = CaptionLabel(
            f"{tr('History DB', '历史数据库', '履歴DB')}: {app_config.history_db_path}",
            self,
        )
        self._db_label.setWordWrap(True)
        self._db_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        set_secondary_text(self._db_label)
        root.addWidget(self._db_label)

        root.addLayout(self._build_browse_row())
        root.addLayout(self._build_filter_row())

        self._summary_label = CaptionLabel("", self)
        self._summary_label.setWordWrap(True)
        self._summary_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        set_secondary_text(self._summary_label)
        root.addWidget(self._summary_label)

        body = FluentSplitter(Qt.Orientation.Horizontal, self)
        body.setChildrenCollapsible(False)
        body.addWidget(self._build_author_panel(body))
        self._view_stack = QStackedWidget(body)
        self._view_stack.addWidget(self._build_table())
        self._view_stack.addWidget(self._build_cover_grid(self._view_stack))
        body.addWidget(self._view_stack)
        body.setStretchFactor(0, 0)
        body.setStretchFactor(1, 1)
        body.setSizes([220, 1200])
        root.addWidget(body, stretch=1)

    def _build_filter_row(self) -> ResponsiveFlowLayout:
        filter_row = ResponsiveFlowLayout()
        filter_row.setSpacing(8)
        self._search_edit = SearchLineEdit(self)
        self._search_edit.setPlaceholderText(
            tr(
                "Search table text...",
                "搜索表格内容...",
                "表の内容を検索...",
            )
        )
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.setMinimumWidth(220)
        self._search_edit.textChanged.connect(self._filter_changed)
        filter_row.addWidget(self._search_edit)

        self._field_combo = ComboBox(self)
        self._field_combo.addItems(
            [
                tr("All Columns", "全部字段", "全列"),
                tr("State", "状态", "状態"),
                tr("Title", "标题", "タイトル"),
                tr("Author", "作者", "作者"),
                tr("Quality", "画质", "画質"),
                tr("Published", "发布日期", "公開日"),
                "ID",
                tr("Source URL", "来源URL", "元URL"),
                tr("Path", "路径", "パス"),
            ]
        )
        self._field_combo.setFixedWidth(150)
        self._field_combo.currentIndexChanged.connect(self._apply_filters)
        filter_row.addWidget(self._field_combo)

        self._state_combo = ComboBox(self)
        self._state_combo.addItems(
            [
                tr("All States", "全部状态", "全状態"),
                tr("OK", "正常", "正常"),
                tr("Moved", "已移走", "移動済み"),
            ]
        )
        self._state_combo.setFixedWidth(130)
        self._state_combo.currentIndexChanged.connect(self._apply_filters)
        filter_row.addWidget(self._state_combo)
        self._add_record_buttons(filter_row)
        return filter_row

    def _add_record_buttons(self, filter_row: ResponsiveFlowLayout):
        clean_btn = PushButton(
            tr("Clean Moved", "清理已移走记录", "移動済みを削除"),
            self,
            FluentIcon.BROOM,
        )
        clean_btn.setToolTip(
            tr(
                "Delete DB records whose files are missing or outside the download folder",
                "删除数据库中已移走/不在下载文件夹的记录；不会删除本地文件",
                "ファイル不明または保存先外のDB履歴を削除します",
            )
        )
        clean_btn.clicked.connect(self._sync_with_download_folder)
        filter_row.addWidget(clean_btn)

        self._delete_selected_btn = PushButton(
            tr("Delete Selected", "删除选中记录", "選択した履歴を削除"),
            self,
            FluentIcon.DELETE,
        )
        self._delete_selected_btn.setEnabled(False)
        self._delete_selected_btn.setToolTip(
            tr(
                "Remove selected history records; local files are untouched",
                "删除选中的历史记录，不会删除本地文件",
                "選択した履歴を削除します。ローカルファイルは削除しません",
            )
        )
        self._delete_selected_btn.clicked.connect(self._remove_selected_record)
        filter_row.addWidget(self._delete_selected_btn)

        export_btn = PushButton(tr("Export CSV", "导出 CSV", "CSV を書き出す"), self, FluentIcon.SHARE)
        export_btn.setToolTip(
            tr(
                "Export the rows currently shown to a CSV file",
                "把当前显示的记录导出为 CSV 文件",
                "現在表示中の行を CSV に書き出します",
            )
        )
        export_btn.clicked.connect(self._export_visible_csv)
        filter_row.addWidget(export_btn)

        columns_btn = ToolButton(FluentIcon.SETTING, self)
        columns_btn.setToolTip(tr("Fields", "字段设置", "列設定"))
        columns_btn.clicked.connect(self._configure_columns)
        filter_row.addWidget(columns_btn)

    def _build_table(self) -> TableWidget:
        self._table = TableWidget(self)
        self._table.setMinimumWidth(0)
        self._table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._table.setObjectName("historyTable")
        self._table.setColumnCount(16)
        self._table.setHorizontalHeaderLabels(
            [
                tr("State", "状态", "状態"),
                tr("Title", "标题", "タイトル"),
                tr("Author", "作者", "作者"),
                tr("Quality", "画质", "画質"),
                tr("Published", "发布日期", "公開日"),
                tr("Likes", "点赞", "いいね"),
                tr("Views", "播放", "再生"),
                tr("Downloaded At", "下载时间", "保存日時"),
                "ID",
                tr("File Path", "文件路径", "ファイルパス"),
                tr("Source URL", "来源URL", "元URL"),
                tr("Page", "页面", "ページ"),
                tr("Folder", "文件夹", "フォルダー"),
                tr("File", "文件", "ファイル"),
                tr("Rename", "重命名", "名前変更"),
                tr("Delete", "删记录", "削除"),
            ]
        )
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        # Keep the native Qt selection behavior so Ctrl-click toggles rows and
        # Shift-click selects a contiguous range.
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setBorderVisible(True)
        self._table.setBorderRadius(8)
        self._table.setWordWrap(False)
        self._table.setShowGrid(False)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(42)
        self._table.cellClicked.connect(self._on_cell_clicked)
        self._table.itemDoubleClicked.connect(self._on_item_double_clicked)
        self._table.itemSelectionChanged.connect(self._update_action_state)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_context_menu)
        self._build_table_header()
        return self._table

    def _build_table_header(self):
        header = self._table.horizontalHeader()
        header.setHighlightSections(False)
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.setSortIndicator(self._sort_column, Qt.SortOrder.DescendingOrder)
        header.sectionClicked.connect(self._on_header_clicked)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        initial_widths = {
            self._COL_STATE: 62,
            self._COL_TITLE: 620,
            self._COL_AUTHOR: 110,
            self._COL_QUALITY: 80,
            self._COL_PUBLISHED: 112,
            self._COL_LIKES: 72,
            self._COL_VIEWS: 78,
            self._COL_DOWNLOADED: 150,
            self._COL_ID: 135,
            self._COL_PATH: 520,
            self._COL_SOURCE_URL: 260,
            self._COL_OPEN_URL: 58,
            self._COL_OPEN_FOLDER: 58,
            self._COL_OPEN_FILE: 58,
            self._COL_RENAME: 66,
            self._COL_REMOVE: 66,
        }
        restore_table_widths(self._table, "history_table_widths", initial_widths)
        connect_table_width_saver(self._table, "history_table_widths")
        restore_table_columns(self._table, "history_table")
        connect_table_column_saver(self._table, "history_table")
        header.sectionResized.connect(lambda *_args: fit_table_last_column(self._table))
        header.sectionMoved.connect(lambda *_args: fit_table_last_column(self._table))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_table"):
            fit_table_last_column(self._table)

    def _load_history(self):
        self._history_refresh_timer.stop()
        self._all_records = download_manager.get_history_records()
        self._records_by_id = {
            str(row.get("video_id", "") or ""): row
            for row in self._all_records
            if str(row.get("video_id", "") or "")
        }
        self._history_dirty = False
        self._rebuild_author_list()
        self._apply_filters()

    def shutdown(self, timeout_ms: int = 30_000) -> bool:
        """Stop the cover downloads of the grid views."""
        return self._cover_grid.shutdown(timeout_ms)

    def _schedule_history_refresh(self, *_args):
        self._history_dirty = True
        # HistoryInterface is constructed with the main window even when its
        # navigation page is hidden.  Avoid reading SQLite and rebuilding a
        # large table for every completion while the user is downloading.
        if self.isVisible() and not self._history_refresh_timer.isActive():
            self._history_refresh_timer.start()

    def draft_state(self) -> dict | None:
        text = self._search_edit.text()
        return {"search": text} if text else None

    def restore_draft(self, state: dict):
        self._search_edit.setText(str(state.get("search") or ""))

    def _filter_changed(self, *_args):
        """Typing in the filter re-renders at most every ~180 ms on big histories."""

        if len(self._all_records) > self.FILTER_DEBOUNCE_ROWS:
            self._filter_timer.start()
        else:
            self._apply_filters()

    def showEvent(self, event: QShowEvent):
        super().showEvent(event)
        if self._render_deferred:
            self._render_deferred = False
            self._render_timer.start(0)
        if self._history_dirty and not self._history_refresh_timer.isActive():
            self._history_refresh_timer.start()

    # Thousands of rows are 16 table items each; building them in one go froze the
    # window for about a second (on every keystroke of the filter, too).  Big
    # lists are filled in ~12 ms slices, and a hidden page waits until shown.
    _ACTION_BY_COLUMN = {
        11: "open_url",  # _COL_OPEN_URL
        12: "folder",
        13: "file",
        14: "rename",
        15: "remove",
    }
    _LONG_COLUMNS = (_COL_TITLE, _COL_AUTHOR, _COL_ID, _COL_PATH)
    RENDER_SYNC_ROWS = 400
    RENDER_SLICE_SECONDS = 0.012
    FILTER_DEBOUNCE_ROWS = 500

    def refresh_theme_styles(self):
        """Re-render rows so baked status/link colors follow the new theme."""
        if hasattr(self, "_cover_grid"):
            self._cover_grid.refresh_theme()
        if hasattr(self, "_table"):
            self._apply_filters()

    def _show_error(self, msg: str):
        InfoBar.error(
            title=tr("Operation Failed", "操作失败", "操作失敗"),
            content=msg,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=4500,
            parent=self,
        )

    def _on_task_status_changed(self, _task_id: str, status_str: str):
        if status_str == TaskStatus.COMPLETED.value:
            self._schedule_history_refresh()
