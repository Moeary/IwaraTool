"""Task Center Interface — table-based download task list."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    ComboBox,
    FluentIcon,
    IconWidget,
    PushButton,
    SearchLineEdit,
    StrongBodyLabel,
    SubtitleLabel,
    SwitchButton,
    TableWidget,
    TitleLabel,
    ToolButton,
)

from ...core.download.speed_format import format_speed
from ...core.manager import download_manager  # noqa: F401 - patch point, see page_dependencies
from ...core.models import DownloadTask
from ...i18n import tr
from ...signal_bus import signal_bus
from ..theme import PAGE_MARGINS, PAGE_SPACING, set_secondary_text
from ..flow_layout import ResponsiveFlowLayout
from ..ui_state import (
    connect_table_column_saver,
    connect_table_width_saver,
    fit_table_last_column,
    restore_table_columns,
    restore_table_widths,
)
from .task_actions import TaskActionsMixin
from .task_live_updates import TaskLiveUpdatesMixin
from .task_menu import TaskMenuMixin
from .task_rows import TaskRowsMixin
from .task_sorting import TaskSortingMixin
from .task_staging import STATE_FILTER_STAGED, TaskStagingMixin
from .task_text import TaskTextMixin


class TaskCenterInterface(
    TaskTextMixin,
    TaskRowsMixin,
    TaskSortingMixin,
    TaskMenuMixin,
    TaskActionsMixin,
    TaskLiveUpdatesMixin,
    TaskStagingMixin,
    QWidget,
):
    """Single-list task center with filters, sorting, and row actions."""

    explain_requested = Signal(str)  # video id: "why was this not downloaded?"

    _COL_STATE = 0
    _COL_TITLE = 1
    _COL_AUTHOR = 2
    _COL_PROGRESS = 3
    _COL_SIZE = 4
    _COL_SPEED = 5
    _COL_QUALITY = 6
    _COL_PRIORITY = 7
    _COL_URL = 8
    _COL_ID = 9
    _COL_ACTION = 10
    _COL_REMOVE = 11

    _SORT_DEFAULT = -1
    _SORT_ADDED = -2

    def __init__(self, parent: QWidget | None = None, *, embedded: bool = False):
        super().__init__(parent)
        self.setObjectName("TaskCenterInterface")
        self._embedded = embedded
        self._tasks_by_id: dict[str, DownloadTask] = {}
        self._row_by_task_id: dict[str, int] = {}
        self._visible_task_ids: list[str] = []
        self._task_order: dict[str, int] = {}
        self._next_order = 0
        self._refresh_pending = False
        self._progress_flush_pending = False
        self._pending_progress_ids: set[str] = set()
        self._sort_column = self._SORT_DEFAULT
        self._sort_reverse = False
        self._init_stage_filters()

        self._build_ui()
        self._connect_signals()
        self._refresh_tasks()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        if self._embedded:
            root.setContentsMargins(0, 0, 0, 0)
            root.setSpacing(8)
        else:
            root.setContentsMargins(*PAGE_MARGINS)
            root.setSpacing(PAGE_SPACING)

        root.addLayout(self._build_title_row())
        root.addLayout(self._build_filter_row())
        root.addLayout(self._build_speed_row())

        self._summary_label = CaptionLabel("", self)
        self._summary_label.setWordWrap(True)
        self._summary_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        set_secondary_text(self._summary_label)
        root.addWidget(self._summary_label)
        root.addWidget(self._build_table(), stretch=1)

    def _build_title_row(self) -> ResponsiveFlowLayout:
        title_row = ResponsiveFlowLayout()
        title_text = tr("Task Center", "任务中心", "タスクセンター")
        # The workbench already shows a page title; a second TitleLabel made
        # the embedded header compete with it.
        title_row.addWidget(SubtitleLabel(title_text, self) if self._embedded else TitleLabel(title_text, self))

        retry_all_btn = PushButton(
            tr("Retry Failed", "重试失败", "失敗を再試行"), self, FluentIcon.SYNC
        )
        retry_all_btn.setToolTip(tr("Retry every failed task", "重试全部失败任务", "失敗したタスクをすべて再試行"))
        retry_all_btn.clicked.connect(self._retry_all_failed)
        title_row.addWidget(retry_all_btn)

        self._exclude_downloaded_switch = SwitchButton(self)
        self._exclude_downloaded_switch.setChecked(True)
        exclude_text = tr("Skip downloaded", "跳过已下载", "保存済みを除外")
        self._exclude_downloaded_switch.setOnText(exclude_text)
        self._exclude_downloaded_switch.setOffText(exclude_text)
        self._exclude_downloaded_switch.setToolTip(
            tr(
                "When retrying, mark tasks whose file already exists as completed",
                "批量重试时，已存在文件的任务直接标记为完成",
                "再試行時、既存ファイルのあるタスクを完了扱いにします",
            )
        )
        title_row.addWidget(self._exclude_downloaded_switch)

        restore_all_btn = PushButton(
            tr("Restore All", "全部恢复", "全件復元"), self, FluentIcon.RETURN
        )
        restore_all_btn.setToolTip(tr("Re-queue every cancelled task", "将全部已中断任务重新排队", "中断済みタスクをすべて再キュー"))
        restore_all_btn.clicked.connect(self._restore_all_cancelled)
        title_row.addWidget(restore_all_btn)

        cancel_all_btn = PushButton(
            tr("Cancel All", "全部中断", "全件中断"), self, FluentIcon.PAUSE
        )
        cancel_all_btn.setToolTip(tr("Interrupt every active task", "中断全部进行中的任务", "実行中のタスクをすべて中断"))
        cancel_all_btn.clicked.connect(self._cancel_all_active)
        title_row.addWidget(cancel_all_btn)

        clear_btn = PushButton(
            tr("Clear Done", "清除完成项", "完了項目をクリア"),
            self,
            FluentIcon.BROOM,
        )
        clear_btn.clicked.connect(self._clear_done)
        title_row.addWidget(clear_btn)

        columns_btn = ToolButton(FluentIcon.SETTING, self)
        columns_btn.setToolTip(tr("Fields", "字段设置", "列設定"))
        columns_btn.clicked.connect(self._configure_columns)
        title_row.addWidget(columns_btn)
        return title_row

    def _build_filter_row(self) -> ResponsiveFlowLayout:
        filter_row = ResponsiveFlowLayout()
        filter_row.setSpacing(8)
        self._search_edit = SearchLineEdit(self)
        self._search_edit.setPlaceholderText(tr("Search tasks...", "搜索任务...", "タスクを検索..."))
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.setMinimumWidth(200)
        self._search_edit.textChanged.connect(self._apply_filters)
        filter_row.addWidget(self._search_edit)

        self._state_combo = ComboBox(self)
        self._state_combo.addItems(
            [
                tr("All States", "全部状态", "全状态"),
                tr("Active", "进行中", "実行中"),
                tr("Queued", "排队中", "待機中"),
                tr("Failed", "失败", "失敗"),
                tr("Completed", "已完成", "完了"),
                tr("Cancelled", "已中断", "中断済み"),
                tr("Skipped", "已跳过", "スキップ"),
                tr("To confirm", "待确认", "確認待ち"),  # STATE_FILTER_STAGED
            ]
        )
        self._state_combo.setFixedWidth(130)
        self._state_combo.currentIndexChanged.connect(self._on_state_filter_changed)
        filter_row.addWidget(self._state_combo)

        self._sort_combo = ComboBox(self)
        self._sort_combo.addItems(
            [
                tr("Task Rank", "任务排行", "タスク順位"),
                tr("State", "状态", "状態"),
                tr("Title", "标题", "タイトル"),
                tr("Author", "作者", "作者"),
                tr("Progress", "进度", "進捗"),
                tr("Size", "大小", "サイズ"),
                tr("Added", "加入顺序", "追加順"),
                tr("Priority", "优先级", "優先度"),
                tr("Quality", "画质", "画質"),
                "ID",
            ]
        )
        self._sort_combo.setFixedWidth(130)
        self._sort_combo.currentIndexChanged.connect(self._on_sort_combo_changed)
        filter_row.addWidget(self._sort_combo)

        self._sort_dir_btn = ToolButton(FluentIcon.UP, self)
        self._sort_dir_btn.setToolTip(tr("Toggle sort direction", "切换升序/降序", "並び順を切替"))
        self._sort_dir_btn.clicked.connect(self._toggle_sort_direction)
        filter_row.addWidget(self._sort_dir_btn)

        priority_group = QWidget(self)
        priority_layout = QHBoxLayout(priority_group)
        priority_layout.setContentsMargins(0, 0, 0, 0)
        priority_layout.setSpacing(6)
        self._priority_combo = ComboBox(priority_group)
        self._priority_combo.addItems(
            [
                tr("High Priority", "高优先级", "高優先度"),
                tr("Normal Priority", "普通优先级", "通常優先度"),
                tr("Low Priority", "低优先级", "低優先度"),
            ]
        )
        self._priority_combo.setItemData(0, 10)
        self._priority_combo.setItemData(1, 0)
        self._priority_combo.setItemData(2, -10)
        self._priority_combo.setCurrentIndex(1)
        self._priority_combo.setFixedWidth(160)
        priority_layout.addWidget(self._priority_combo)
        priority_btn = PushButton(
            tr("Apply to selected", "应用到所选", "選択に適用"), priority_group
        )
        priority_btn.clicked.connect(self._set_selected_priority)
        priority_layout.addWidget(priority_btn)
        filter_row.addWidget(priority_group)
        return filter_row

    def _build_speed_row(self) -> QHBoxLayout:
        speed_row = QHBoxLayout()
        speed_row.setSpacing(8)
        self._speed_icon = IconWidget(FluentIcon.SPEED_HIGH, self)
        self._speed_icon.setFixedSize(18, 18)
        speed_row.addWidget(self._speed_icon)
        speed_row.addWidget(BodyLabel(tr("Total speed", "总下载速度", "合計速度"), self))
        self._total_speed_label = StrongBodyLabel(format_speed(0), self)
        self._total_speed_label.setMinimumWidth(96)
        speed_row.addWidget(self._total_speed_label)
        self._speed_hint = CaptionLabel("", self)
        set_secondary_text(self._speed_hint)
        speed_row.addWidget(self._speed_hint)
        speed_row.addStretch(1)
        return speed_row

    def _build_table(self) -> TableWidget:
        self._table = TableWidget(self)
        self._table.setMinimumWidth(0)
        self._table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._table.setObjectName("taskTable")
        self._table.setColumnCount(12)
        self._table.setHorizontalHeaderLabels(
            [
                tr("State", "状态", "状態"),
                tr("Title", "标题", "タイトル"),
                tr("Author", "作者", "作者"),
                tr("Progress", "进度", "進捗"),
                tr("Size", "大小", "サイズ"),
                tr("Speed", "速度", "速度"),
                tr("Quality", "画质", "画質"),
                tr("Priority", "优先级", "優先度"),
                "URL",
                "ID",
                tr("Action", "操作", "操作"),
                tr("Remove", "移除", "削除"),
            ]
        )
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        # ExtendedSelection gives the table native Ctrl-click toggles and
        # Shift-click range selection without a second selection model.
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setBorderVisible(True)
        self._table.setBorderRadius(8)
        self._table.setWordWrap(False)
        self._table.setShowGrid(False)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(34)
        self._table.itemDoubleClicked.connect(self._on_item_double_clicked)
        self._table.cellClicked.connect(self._on_cell_clicked)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_context_menu)

        header = self._table.horizontalHeader()
        header.setHighlightSections(False)
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.sectionClicked.connect(self._on_header_clicked)
        self._restore_sort_indicator()
        self._restore_table_layout(header)
        return self._table

    def _restore_table_layout(self, header: QHeaderView):
        default_widths = {
            self._COL_STATE: 72,
            self._COL_TITLE: 320,
            self._COL_AUTHOR: 116,
            self._COL_PROGRESS: 180,
            self._COL_SIZE: 142,
            self._COL_SPEED: 96,
            self._COL_QUALITY: 72,
            self._COL_PRIORITY: 82,
            self._COL_URL: 68,
            self._COL_ID: 126,
            self._COL_ACTION: 66,
            self._COL_REMOVE: 66,
        }
        restore_table_widths(self._table, "task_table_widths", default_widths)
        connect_table_width_saver(self._table, "task_table_widths")
        restore_table_columns(self._table, "task_table")
        connect_table_column_saver(self._table, "task_table")
        header.sectionResized.connect(lambda *_args: fit_table_last_column(self._table))
        header.sectionMoved.connect(lambda *_args: fit_table_last_column(self._table))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_table"):
            fit_table_last_column(self._table)

    def _on_state_filter_changed(self, _index: int):
        if self._state_combo.currentIndex() != STATE_FILTER_STAGED:
            self._stage_verdict_filter = self._stage_batch_filter = ""  # those belong to "To confirm"
        self._apply_filters()

    def refresh_theme_styles(self):
        """Repaint status/link colors that were baked into table items."""
        if hasattr(self, "_table"):
            self._apply_filters()

    # ── Signals ───────────────────────────────────────────────────────────────

    def _connect_signals(self):
        signal_bus.tasks_added.connect(self._on_tasks_added)
        signal_bus.task_added.connect(self._on_task_added)
        signal_bus.task_status_changed.connect(self._on_task_status_changed)
        signal_bus.task_progress_updated.connect(self._on_task_progress)
        signal_bus.task_error.connect(self._on_task_error)
        signal_bus.tasks_removed.connect(self._on_tasks_removed)
        signal_bus.task_removed.connect(self._on_task_removed)
        signal_bus.task_priority_changed.connect(self._on_task_priority_changed)
