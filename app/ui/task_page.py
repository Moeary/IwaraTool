"""Task Center Interface — table-based download task list."""
from __future__ import annotations

import webbrowser
from typing import Any

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QTableWidgetItem,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from qfluentwidgets import (
    Action,
    BodyLabel,
    ComboBox,
    FluentIcon,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    PrimaryPushButton,
    RoundMenu,
    SwitchButton,
    TableWidget,
    TitleLabel,
    ToolButton,
)

from ..core.manager import download_manager
from ..core.models import DownloadTask, TaskStatus, status_label
from ..i18n import tr
from ..signal_bus import signal_bus
from .ui_state import (
    ResponsiveFlowLayout,
    connect_table_column_saver,
    connect_table_width_saver,
    fit_table_last_column,
    open_table_column_dialog,
    restore_table_columns,
    restore_table_widths,
)


_STATUS_COLORS: dict[TaskStatus, str] = {
    TaskStatus.QUEUED_META: "#6b6b6b",
    TaskStatus.RESOLVING: "#0078d4",
    TaskStatus.QUEUED_DOWNLOAD: "#8764b8",
    TaskStatus.DOWNLOADING: "#107c10",
    TaskStatus.CANCELLING: "#c17d00",
    TaskStatus.CANCELLED: "#666666",
    TaskStatus.SKIPPED: "#c17d00",
    TaskStatus.COMPLETED: "#107c10",
    TaskStatus.FAILED: "#c42b1c",
}

_DEFAULT_STATUS_PRIORITY: dict[TaskStatus, int] = {
    TaskStatus.DOWNLOADING: 0,
    TaskStatus.RESOLVING: 1,
    TaskStatus.CANCELLING: 2,
    TaskStatus.QUEUED_DOWNLOAD: 3,
    TaskStatus.QUEUED_META: 4,
    TaskStatus.FAILED: 5,
    TaskStatus.SKIPPED: 6,
    TaskStatus.CANCELLED: 7,
    TaskStatus.COMPLETED: 8,
}

_ACTIVE_STATUSES = frozenset(
    {
        TaskStatus.RESOLVING,
        TaskStatus.QUEUED_DOWNLOAD,
        TaskStatus.DOWNLOADING,
        TaskStatus.CANCELLING,
    }
)

_TERMINAL_STATUSES = frozenset(
    {
        TaskStatus.COMPLETED,
        TaskStatus.SKIPPED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    }
)


class TaskCenterInterface(QWidget):
    """Single-list task center with filters, sorting, and row actions."""

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
            root.setContentsMargins(36, 24, 36, 16)
            root.setSpacing(12)

        title_row = ResponsiveFlowLayout()
        title_row.addWidget(TitleLabel(tr("Task Center", "任务中心", "タスクセンター"), self))

        self._exclude_downloaded_switch = SwitchButton(self)
        self._exclude_downloaded_switch.setChecked(True)
        title_row.addWidget(BodyLabel(tr("Exclude downloaded", "排除已下载", "ダウンロード済みを除外"), self))
        title_row.addWidget(self._exclude_downloaded_switch)

        retry_all_btn = PrimaryPushButton(
            tr("Retry All", "全部重试", "全件再試行"), self, FluentIcon.SYNC
        )
        retry_all_btn.clicked.connect(self._retry_all_failed)
        title_row.addWidget(retry_all_btn)

        restore_all_btn = PrimaryPushButton(
            tr("Restore All", "全部恢复", "全件復元"), self, FluentIcon.RETURN
        )
        restore_all_btn.clicked.connect(self._restore_all_cancelled)
        title_row.addWidget(restore_all_btn)

        cancel_all_btn = PrimaryPushButton(
            tr("Cancel All", "全部中断", "全件中断"), self, FluentIcon.CANCEL
        )
        cancel_all_btn.clicked.connect(self._cancel_all_active)
        title_row.addWidget(cancel_all_btn)

        clear_btn = PrimaryPushButton(
            tr("Clear Done", "清除完成项", "完了項目をクリア"),
            self,
            FluentIcon.BROOM,
        )
        clear_btn.clicked.connect(self._clear_done)
        title_row.addWidget(clear_btn)

        columns_btn = PrimaryPushButton(
            tr("Fields", "字段设置", "列設定"), self, FluentIcon.SETTING
        )
        columns_btn.clicked.connect(self._configure_columns)
        title_row.addWidget(columns_btn)
        root.addLayout(title_row)

        filter_row = ResponsiveFlowLayout()
        self._search_edit = LineEdit(self)
        self._search_edit.setPlaceholderText(tr("Search tasks...", "搜索任务...", "タスクを検索..."))
        self._search_edit.setClearButtonEnabled(True)
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
            ]
        )
        self._state_combo.setFixedWidth(130)
        self._state_combo.currentIndexChanged.connect(self._apply_filters)
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

        self._priority_combo = ComboBox(self)
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
        self._priority_combo.setFixedWidth(130)
        filter_row.addWidget(self._priority_combo)
        priority_btn = PrimaryPushButton(
            tr("Set Priority", "设置优先级", "優先度を設定"), self
        )
        priority_btn.clicked.connect(self._set_selected_priority)
        filter_row.addWidget(priority_btn)

        root.addLayout(filter_row)

        self._summary_label = BodyLabel("", self)
        self._summary_label.setWordWrap(True)
        self._summary_label.setMinimumHeight(28)
        self._summary_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        root.addWidget(self._summary_label)

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

        root.addWidget(self._table, stretch=1)

    def _configure_columns(self):
        open_table_column_dialog(
            self._table,
            "task_table",
            title=tr("Task Columns", "任务列表字段", "タスク列設定"),
            parent=self,
        )
        fit_table_last_column(self._table)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_table"):
            fit_table_last_column(self._table)

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

    # ── Rendering ─────────────────────────────────────────────────────────────

    def _schedule_refresh(self, delay_ms: int = 80):
        if self._refresh_pending:
            return
        self._refresh_pending = True
        QTimer.singleShot(delay_ms, self._refresh_tasks)

    def _refresh_tasks(self):
        self._refresh_pending = False
        tasks = download_manager.get_tasks()
        for task in tasks:
            self._ensure_order(task.task_id)
        self._tasks_by_id = {task.task_id: task for task in tasks}
        self._apply_filters()

    def _apply_filters(self, *_args):
        tasks = list(self._tasks_by_id.values())
        visible = [task for task in tasks if self._passes_filters(task)]
        visible = self._sort_tasks(visible)
        self._render_table(visible)
        self._update_summary(tasks, visible)

    def _render_table(self, tasks: list[DownloadTask]):
        self._table.setUpdatesEnabled(False)
        try:
            self._row_by_task_id = {}
            self._visible_task_ids = [task.task_id for task in tasks]
            self._table.setRowCount(len(tasks))
            for row_idx, task in enumerate(tasks):
                self._row_by_task_id[task.task_id] = row_idx
                self._update_row(row_idx, task)
        finally:
            self._table.setUpdatesEnabled(True)

    def _update_row(self, row_idx: int, task: DownloadTask):
        values = [
            status_label(task.status),
            task.title or task.video_id,
            task.author,
            self._progress_text(task),
            self._size_text(task),
            task.speed_str,
            task.quality,
            self._priority_text(task.priority),
            _video_url(task.video_id),
            task.video_id,
        ]

        for col_idx, value in enumerate(values):
            item = self._table.item(row_idx, col_idx)
            if item is None:
                item = QTableWidgetItem()
                self._table.setItem(row_idx, col_idx, item)
            item.setText(value)
            item.setData(Qt.ItemDataRole.UserRole, task.task_id)
            item.setToolTip(self._cell_tooltip(task, col_idx, value))
            if col_idx == self._COL_STATE:
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item.setForeground(QColor(_STATUS_COLORS.get(task.status, "#666666")))
            elif col_idx in (self._COL_PROGRESS, self._COL_SIZE, self._COL_SPEED):
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            elif col_idx == self._COL_PRIORITY:
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            else:
                item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        action_key, action_text, action_tip, action_enabled = self._primary_action(task)
        task_url = _video_url(task.video_id)
        self._set_action_item(
            row_idx,
            self._COL_URL,
            task.task_id,
            "open_url",
            tr("Open", "打开", "開く"),
            task_url or tr("No video URL", "没有视频链接", "動画URLがありません"),
            bool(task_url),
            action_url=task_url,
        )
        self._set_action_item(
            row_idx,
            self._COL_ACTION,
            task.task_id,
            action_key,
            action_text,
            action_tip,
            action_enabled,
        )
        self._set_action_item(
            row_idx,
            self._COL_REMOVE,
            task.task_id,
            "remove",
            tr("Remove", "移除", "削除"),
            tr("Remove task", "移除任务", "タスクを削除"),
            True,
        )

    def _set_action_item(
        self,
        row: int,
        column: int,
        task_id: str,
        action: str,
        text: str,
        tooltip: str,
        enabled: bool,
        *,
        action_url: str = "",
    ):
        item = self._table.item(row, column)
        if item is None:
            item = QTableWidgetItem()
            self._table.setItem(row, column, item)
        item.setText(text if enabled else "—")
        item.setData(Qt.ItemDataRole.UserRole, task_id)
        item.setData(Qt.ItemDataRole.UserRole + 1, action if enabled else "")
        item.setData(Qt.ItemDataRole.UserRole + 2, action_url if enabled else "")
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        item.setToolTip(tooltip)
        item.setForeground(QColor("#0078d4" if enabled else "#999999"))

    def _update_summary(self, tasks: list[DownloadTask], visible: list[DownloadTask]):
        active = sum(1 for task in tasks if task.status in _ACTIVE_STATUSES)
        queued = sum(1 for task in tasks if task.status in (TaskStatus.QUEUED_META, TaskStatus.QUEUED_DOWNLOAD))
        failed = sum(1 for task in tasks if task.status == TaskStatus.FAILED)
        cancelled = sum(1 for task in tasks if task.status == TaskStatus.CANCELLED)
        skipped = sum(1 for task in tasks if task.status == TaskStatus.SKIPPED)
        completed = sum(1 for task in tasks if task.status == TaskStatus.COMPLETED)
        self._summary_label.setText(
            tr(
                f"Tasks: {len(tasks)} | visible: {len(visible)} | active: {active} | queued: {queued} | failed: {failed} | cancelled: {cancelled} | skipped: {skipped} | completed: {completed}",
                f"任务: {len(tasks)} | 当前显示: {len(visible)} | 进行中: {active} | 排队: {queued} | 失败: {failed} | 中断: {cancelled} | 跳过: {skipped} | 完成: {completed}",
                f"タスク: {len(tasks)} | 表示: {len(visible)} | 実行中: {active} | 待機: {queued} | 失敗: {failed} | 中断: {cancelled} | スキップ: {skipped} | 完了: {completed}",
            )
        )

    def _update_current_summary(self):
        tasks = list(self._tasks_by_id.values())
        visible = [
            self._tasks_by_id[task_id]
            for task_id in self._visible_task_ids
            if task_id in self._tasks_by_id
        ]
        self._update_summary(tasks, visible)

    def _task_from_info(self, info: dict) -> DownloadTask:
        task_id = str(info.get("task_id", "") or "")
        video_id = str(info.get("video_id", "") or task_id)
        status_value = str(info.get("status", TaskStatus.QUEUED_META.value) or TaskStatus.QUEUED_META.value)
        try:
            status = TaskStatus(status_value)
        except ValueError:
            status = TaskStatus.QUEUED_META
        return DownloadTask(
            task_id=task_id,
            url=f"https://www.iwara.tv/video/{video_id}",
            video_id=video_id,
            title=str(info.get("title", "") or video_id),
            author=str(info.get("author", "") or ""),
            status=status,
            priority=int(info.get("priority", 0) or 0),
        )

    def _fetch_task(self, task_id: str, info: dict | None = None) -> DownloadTask | None:
        task = download_manager.get_task(task_id)
        if task:
            return task
        if info is not None:
            return self._task_from_info(info)
        return self._tasks_by_id.get(task_id)

    def _append_visible_tasks(self, tasks: list[DownloadTask]):
        visible = [
            task
            for task in tasks
            if task.task_id not in self._row_by_task_id and self._passes_filters(task)
        ]
        if not visible:
            self._update_current_summary()
            return
        start = self._table.rowCount()
        self._table.setUpdatesEnabled(False)
        try:
            self._table.setRowCount(start + len(visible))
            for offset, task in enumerate(visible):
                row = start + offset
                self._visible_task_ids.append(task.task_id)
                self._row_by_task_id[task.task_id] = row
                self._update_row(row, task)
        finally:
            self._table.setUpdatesEnabled(True)
        self._update_current_summary()

    def _upsert_task_row(self, task: DownloadTask):
        row = self._row_by_task_id.get(task.task_id)
        visible = self._passes_filters(task)
        if visible and row is not None:
            self._update_row(row, task)
        elif visible:
            self._append_visible_tasks([task])
            return
        elif row is not None:
            self._remove_visible_task_ids([task.task_id])
            return
        self._update_current_summary()

    def _remove_visible_task_ids(self, task_ids: list[str]):
        remove_set = {task_id for task_id in task_ids if task_id}
        if not remove_set:
            return
        rows = sorted(
            (row for task_id, row in self._row_by_task_id.items() if task_id in remove_set),
            reverse=True,
        )
        if rows:
            self._table.setUpdatesEnabled(False)
            try:
                for row in rows:
                    self._table.removeRow(row)
            finally:
                self._table.setUpdatesEnabled(True)
        self._visible_task_ids = [task_id for task_id in self._visible_task_ids if task_id not in remove_set]
        self._rebuild_row_map()
        self._update_current_summary()

    def _rebuild_row_map(self):
        self._row_by_task_id = {task_id: row for row, task_id in enumerate(self._visible_task_ids)}

    # ── Sorting / filtering ──────────────────────────────────────────────────

    def _passes_filters(self, task: DownloadTask) -> bool:
        state_idx = self._state_combo.currentIndex() if hasattr(self, "_state_combo") else 0
        if state_idx == 1 and task.status not in _ACTIVE_STATUSES:
            return False
        if state_idx == 2 and task.status not in (TaskStatus.QUEUED_META, TaskStatus.QUEUED_DOWNLOAD):
            return False
        if state_idx == 3 and task.status != TaskStatus.FAILED:
            return False
        if state_idx == 4 and task.status != TaskStatus.COMPLETED:
            return False
        if state_idx == 5 and task.status != TaskStatus.CANCELLED:
            return False
        if state_idx == 6 and task.status != TaskStatus.SKIPPED:
            return False

        query = self._search_edit.text().strip().lower() if hasattr(self, "_search_edit") else ""
        if query and query not in self._task_search_text(task):
            return False
        return True

    def _sort_tasks(self, tasks: list[DownloadTask]) -> list[DownloadTask]:
        def text(value: Any) -> str:
            return str(value or "").lower()

        def key(task: DownloadTask):
            order = self._task_order.get(task.task_id, 0)
            progress = self._progress_ratio(task)
            if self._sort_column == self._SORT_DEFAULT:
                return (_DEFAULT_STATUS_PRIORITY.get(task.status, 99), -task.priority, order)
            if self._sort_column == self._SORT_ADDED:
                return order
            if self._sort_column == self._COL_STATE:
                return (_DEFAULT_STATUS_PRIORITY.get(task.status, 99), text(status_label(task.status)), order)
            if self._sort_column == self._COL_TITLE:
                return (text(task.title or task.video_id), order)
            if self._sort_column == self._COL_AUTHOR:
                return (text(task.author), order)
            if self._sort_column == self._COL_PROGRESS:
                return (progress, order)
            if self._sort_column == self._COL_SIZE:
                return (task.total_bytes or task.downloaded_bytes or 0, order)
            if self._sort_column == self._COL_SPEED:
                return (text(task.speed_str), order)
            if self._sort_column == self._COL_QUALITY:
                return (text(task.quality), order)
            if self._sort_column == self._COL_PRIORITY:
                return (task.priority, order)
            if self._sort_column == self._COL_URL:
                return (text(_video_url(task.video_id)), order)
            if self._sort_column == self._COL_ID:
                return (text(task.video_id), order)
            return (_DEFAULT_STATUS_PRIORITY.get(task.status, 99), -task.priority, order)

        return sorted(tasks, key=key, reverse=self._sort_reverse)

    def _on_sort_combo_changed(self, index: int):
        mapping = {
            0: self._SORT_DEFAULT,
            1: self._COL_STATE,
            2: self._COL_TITLE,
            3: self._COL_AUTHOR,
            4: self._COL_PROGRESS,
            5: self._COL_SIZE,
            6: self._SORT_ADDED,
            7: self._COL_PRIORITY,
            8: self._COL_QUALITY,
            9: self._COL_ID,
        }
        self._sort_column = mapping.get(index, self._SORT_DEFAULT)
        if index in (4, 5, 7):
            self._sort_reverse = True
        elif index == 0:
            self._sort_reverse = False
        self._update_sort_button()
        self._restore_sort_indicator()
        self._apply_filters()

    def _on_header_clicked(self, column: int):
        if column not in self._sortable_columns():
            self._restore_sort_indicator()
            QTimer.singleShot(0, self._restore_sort_indicator)
            return
        if column == self._sort_column:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_column = column
            self._sort_reverse = column in (
                self._COL_PROGRESS,
                self._COL_SIZE,
                self._COL_PRIORITY,
            )
        self._sync_sort_combo()
        self._update_sort_button()
        self._restore_sort_indicator()
        self._apply_filters()

    def _toggle_sort_direction(self):
        self._sort_reverse = not self._sort_reverse
        self._update_sort_button()
        self._restore_sort_indicator()
        self._apply_filters()

    def _sortable_columns(self) -> set[int]:
        return {
            self._COL_STATE,
            self._COL_TITLE,
            self._COL_AUTHOR,
            self._COL_PROGRESS,
            self._COL_SIZE,
            self._COL_SPEED,
            self._COL_QUALITY,
            self._COL_PRIORITY,
            self._COL_URL,
            self._COL_ID,
        }

    def _restore_sort_indicator(self):
        column = self._COL_STATE if self._sort_column in (self._SORT_DEFAULT, self._SORT_ADDED) else self._sort_column
        self._table.horizontalHeader().setSortIndicator(
            column,
            Qt.SortOrder.DescendingOrder if self._sort_reverse else Qt.SortOrder.AscendingOrder,
        )

    def _sync_sort_combo(self):
        mapping = {
            self._COL_STATE: 1,
            self._COL_TITLE: 2,
            self._COL_AUTHOR: 3,
            self._COL_PROGRESS: 4,
            self._COL_SIZE: 5,
            self._SORT_ADDED: 6,
            self._COL_PRIORITY: 7,
            self._COL_QUALITY: 8,
            self._COL_ID: 9,
        }
        self._sort_combo.blockSignals(True)
        self._sort_combo.setCurrentIndex(mapping.get(self._sort_column, 0))
        self._sort_combo.blockSignals(False)

    def _update_sort_button(self):
        self._sort_dir_btn.setIcon(FluentIcon.DOWN if self._sort_reverse else FluentIcon.UP)
        self._sort_dir_btn.setToolTip(
            tr("Descending", "降序", "降順") if self._sort_reverse else tr("Ascending", "升序", "昇順")
        )

    # ── Row actions ───────────────────────────────────────────────────────────

    def _primary_action(self, task: DownloadTask):
        if task.status == TaskStatus.FAILED:
            return (
                "retry",
                tr("Retry", "重试", "再試行"),
                tr("Retry task", "重试任务", "タスクを再試行"),
                True,
            )
        if task.status == TaskStatus.COMPLETED:
            return (
                "open",
                tr("Open", "打开", "開く"),
                tr("Open downloaded file", "打开下载文件", "保存ファイルを開く"),
                bool(task.file_path),
            )
        if task.status == TaskStatus.CANCELLED:
            return (
                "restore",
                tr("Restore", "复原", "復元"),
                tr(
                    "Put cancelled task back into the queue",
                    "将已中断任务重新加入队列",
                    "中断済みタスクをキューに戻します",
                ),
                True,
            )
        if task.status in _TERMINAL_STATUSES:
            return (
                "",
                tr("None", "无", "なし"),
                tr("No action", "无可用操作", "操作なし"),
                False,
            )
        return (
            "cancel",
            tr("Cancel", "中断", "中断"),
            tr("Cancel task", "中断任务", "タスクを中断"),
            task.status != TaskStatus.CANCELLING,
        )

    def _on_cell_clicked(self, row: int, column: int):
        if column not in (self._COL_URL, self._COL_ACTION, self._COL_REMOVE):
            return
        item = self._table.item(row, column)
        if not item:
            return
        task_id = str(item.data(Qt.ItemDataRole.UserRole) or "")
        action = str(item.data(Qt.ItemDataRole.UserRole + 1) or "")
        if not task_id or not action:
            return
        if action == "open_url":
            self._open_url(str(item.data(Qt.ItemDataRole.UserRole + 2) or ""))
        elif action == "retry":
            self._retry_task(task_id)
        elif action == "open":
            self._open_task(task_id)
        elif action == "cancel":
            self._cancel_task(task_id)
        elif action == "restore":
            self._restore_task(task_id)
        elif action == "remove":
            self._remove_task(task_id)

    def _selected_task_ids(self) -> list[str]:
        if not hasattr(self, "_table"):
            return []
        rows = sorted({index.row() for index in self._table.selectionModel().selectedRows()})
        task_ids = [
            self._visible_task_ids[row]
            for row in rows
            if 0 <= row < len(self._visible_task_ids)
        ]
        if task_ids:
            return task_ids
        row = self._table.currentRow()
        if 0 <= row < len(self._visible_task_ids):
            return [self._visible_task_ids[row]]
        return []

    def _selected_tasks_by_status(self, task_ids: list[str]) -> dict[TaskStatus, list[str]]:
        grouped: dict[TaskStatus, list[str]] = {}
        for task_id in task_ids:
            task = self._tasks_by_id.get(task_id)
            if task is None:
                continue
            grouped.setdefault(task.status, []).append(task_id)
        return grouped

    def _show_context_menu(self, position):
        item = self._table.itemAt(position)
        if item is not None:
            selected_rows = {index.row() for index in self._table.selectionModel().selectedRows()}
            if item.row() not in selected_rows:
                self._table.clearSelection()
                self._table.selectRow(item.row())

        task_ids = self._selected_task_ids()
        if not task_ids:
            return
        grouped = self._selected_tasks_by_status(task_ids)
        completed_ids = grouped.get(TaskStatus.COMPLETED, [])
        failed_ids = grouped.get(TaskStatus.FAILED, [])
        cancelled_ids = grouped.get(TaskStatus.CANCELLED, [])
        cancelling_ids = grouped.get(TaskStatus.CANCELLING, [])
        interruptible_ids = [
            task_id
            for status, ids in grouped.items()
            if status in _ACTIVE_STATUSES and status != TaskStatus.CANCELLING
            for task_id in ids
        ]

        menu = RoundMenu(parent=self)
        if completed_ids:
            if len(completed_ids) == 1:
                completed_id = completed_ids[0]
                completed_task = self._tasks_by_id.get(completed_id)
                video_id = completed_task.video_id if completed_task else ""
                menu.addAction(
                    Action(
                        FluentIcon.HISTORY,
                        tr("Open video page", "打开视频页面", "動画ページを開く"),
                        self,
                        triggered=lambda _checked=False, url=_video_url(video_id): self._open_url(url),
                    )
                )
                menu.addAction(
                    Action(
                        FluentIcon.FOLDER,
                        tr("Open folder", "打开文件夹", "フォルダーを開く"),
                        self,
                        triggered=lambda _checked=False, selected_id=completed_id: self._open_task_output(
                            selected_id, open_file=False
                        ),
                    )
                )
                menu.addAction(
                    Action(
                        FluentIcon.DOCUMENT,
                        tr("Open file", "打开文件", "ファイルを開く"),
                        self,
                        triggered=lambda _checked=False, selected_id=completed_id: self._open_task_output(
                            selected_id, open_file=True
                        ),
                    )
                )
            else:
                menu.addAction(
                    Action(
                        FluentIcon.HISTORY,
                        tr(
                            f"Open selected video pages ({len(completed_ids)})",
                            f"打开所选视频页面（{len(completed_ids)}）",
                            f"選択した動画ページを開く（{len(completed_ids)}）",
                        ),
                        self,
                        triggered=lambda _checked=False, ids=tuple(completed_ids): self._open_task_video_pages(ids),
                    )
                )
        if completed_ids and (
            failed_ids or cancelled_ids or interruptible_ids or cancelling_ids
        ):
            menu.addSeparator()
        if failed_ids:
            menu.addAction(
                Action(
                    FluentIcon.SYNC,
                    tr(
                        f"Retry selected failed ({len(failed_ids)})",
                        f"重试所选失败任务（{len(failed_ids)}）",
                        f"選択した失敗タスクを再試行（{len(failed_ids)}）",
                    ),
                    self,
                    triggered=lambda _checked=False, ids=tuple(failed_ids): self._retry_task_ids(ids),
                )
            )
        if cancelled_ids:
            menu.addAction(
                Action(
                    FluentIcon.RETURN,
                    tr(
                        f"Restore selected cancelled ({len(cancelled_ids)})",
                        f"恢复所选中断任务（{len(cancelled_ids)}）",
                        f"選択した中断タスクを復元（{len(cancelled_ids)}）",
                    ),
                    self,
                    triggered=lambda _checked=False, ids=tuple(cancelled_ids): self._restore_task_ids(ids),
                )
            )
        if interruptible_ids:
            menu.addAction(
                Action(
                    FluentIcon.CANCEL,
                    tr(
                        f"Interrupt selected active ({len(interruptible_ids)})",
                        f"中断所选进行中任务（{len(interruptible_ids)}）",
                        f"選択した実行中タスクを中断（{len(interruptible_ids)}）",
                    ),
                    self,
                    triggered=lambda _checked=False, ids=tuple(interruptible_ids): self._cancel_task_ids(ids),
                )
            )
        if cancelling_ids and not interruptible_ids:
            # Keep the menu honest for rows that are already waiting for a
            # cancellation callback; requesting it again has no effect.
            menu.addAction(
                Action(
                    FluentIcon.INFO,
                    tr(
                        f"Cancellation already requested ({len(cancelling_ids)})",
                        f"已请求中断（{len(cancelling_ids)}）",
                        f"中断要求済み（{len(cancelling_ids)}）",
                    ),
                    self,
                    triggered=lambda _checked=False: None,
                )
            )
        if menu.actions():
            menu.addSeparator()
        menu.addAction(
            Action(
                FluentIcon.DELETE,
                tr(
                    f"Remove selected tasks ({len(task_ids)})",
                    f"移除所选任务（{len(task_ids)}）",
                    f"選択したタスクを削除（{len(task_ids)}）",
                ),
                self,
                triggered=lambda _checked=False, ids=tuple(task_ids): self._remove_task_ids(ids),
            )
        )
        menu.exec(self._table.viewport().mapToGlobal(position))

    def _set_selected_priority(self):
        task_ids = self._selected_task_ids()
        if not task_ids:
            InfoBar.warning(
                title=tr("Select a task", "请选择任务", "タスクを選択してください"),
                content="",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=1800,
                parent=self,
            )
            return
        priority = int(self._priority_combo.currentData() or 0)
        for task_id in task_ids:
            download_manager.set_task_priority(task_id, priority)
        self._schedule_refresh(0)

    def _retry_task(self, task_id: str):
        download_manager.retry_task(task_id)

    def _cancel_task(self, task_id: str):
        download_manager.cancel_task(task_id)

    def _restore_task(self, task_id: str):
        restored = download_manager.restore_cancelled_task(task_id)
        if not restored:
            InfoBar.warning(
                title=tr("Cannot restore", "无法复原", "復元できません"),
                content=tr(
                    "Only cancelled tasks can be restored",
                    "只有已中断任务可以复原",
                    "中断済みタスクのみ復元できます",
                ),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=2500,
                parent=self,
            )

    def _remove_task(self, task_id: str):
        download_manager.remove_task(task_id)

    def _retry_task_ids(self, task_ids: tuple[str, ...] | list[str]):
        ids = [
            task_id
            for task_id in task_ids
            if self._tasks_by_id.get(task_id)
            and self._tasks_by_id[task_id].status == TaskStatus.FAILED
        ]
        for task_id in ids:
            download_manager.retry_task(task_id)
        self._schedule_refresh(0)
        self._show_bulk_feedback(
            tr("Retry requested", "已请求重试", "再試行を要求しました"),
            tr(
                f"Retried {len(ids)} failed tasks",
                f"已重试 {len(ids)} 个失败任务",
                f"失敗タスク {len(ids)} 件を再試行しました",
            ),
        )

    def _restore_task_ids(self, task_ids: tuple[str, ...] | list[str]):
        restored = 0
        for task_id in task_ids:
            task = self._tasks_by_id.get(task_id)
            if task is None or task.status != TaskStatus.CANCELLED:
                continue
            if download_manager.restore_cancelled_task(task_id):
                restored += 1
        self._schedule_refresh(0)
        self._show_bulk_feedback(
            tr("Restore requested", "已请求恢复", "復元を要求しました"),
            tr(
                f"Restored {restored} cancelled tasks",
                f"已恢复 {restored} 个中断任务",
                f"中断タスク {restored} 件を復元しました",
            ),
        )

    def _cancel_task_ids(self, task_ids: tuple[str, ...] | list[str]):
        cancelled = 0
        for task_id in task_ids:
            task = self._tasks_by_id.get(task_id)
            if task is None or task.status not in _ACTIVE_STATUSES or task.status == TaskStatus.CANCELLING:
                continue
            if download_manager.cancel_task(task_id):
                cancelled += 1
        self._schedule_refresh(0)
        self._show_bulk_feedback(
            tr("Interrupt requested", "已请求中断", "中断を要求しました"),
            tr(
                f"Requested interruption for {cancelled} active tasks",
                f"已请求中断 {cancelled} 个进行中任务",
                f"実行中タスク {cancelled} 件に中断を要求しました",
            ),
        )

    def _remove_task_ids(self, task_ids: tuple[str, ...] | list[str]):
        ids = [task_id for task_id in task_ids if task_id in self._tasks_by_id]
        for task_id in ids:
            download_manager.remove_task(task_id)
        self._schedule_refresh(0)
        self._show_bulk_feedback(
            tr("Tasks removed", "任务已移除", "タスクを削除しました"),
            tr(
                f"Removed {len(ids)} selected tasks",
                f"已移除 {len(ids)} 个所选任务",
                f"選択したタスク {len(ids)} 件を削除しました",
            ),
        )

    def _show_bulk_feedback(self, title: str, content: str):
        InfoBar.info(
            title=title,
            content=content,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=2500,
            parent=self,
        )

    def _open_task(self, task_id: str):
        ok, message = download_manager.open_task_output(task_id)
        if not ok:
            self._show_open_error(message)

    def _open_task_output(self, task_id: str, *, open_file: bool):
        ok, message = download_manager.open_task_output(task_id, open_file=open_file)
        if not ok:
            self._show_open_error(message)

    def _show_open_error(self, message: str):
        InfoBar.warning(
            title=tr("Cannot open", "无法打开", "開けません"),
            content=message,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=2500,
            parent=self,
        )

    def _open_task_video_pages(self, task_ids: tuple[str, ...] | list[str]):
        for task_id in task_ids:
            task = self._tasks_by_id.get(task_id)
            if task:
                self._open_url(_video_url(task.video_id))

    def _open_url(self, url: str):
        if url:
            webbrowser.open(url)

    def _on_item_double_clicked(self, item: QTableWidgetItem):
        task_id = str(item.data(Qt.ItemDataRole.UserRole) or "")
        if item.column() == self._COL_URL:
            self._open_url(str(item.data(Qt.ItemDataRole.UserRole + 2) or ""))
            return
        task = self._tasks_by_id.get(task_id)
        if task and task.status == TaskStatus.COMPLETED:
            self._open_task(task_id)

    # ── Toolbar actions ───────────────────────────────────────────────────────

    def _clear_done(self):
        download_manager.clear_completed()
        self._schedule_refresh(0)
        InfoBar.success(
            title=tr("Cleared", "已清除", "クリア完了"),
            content=tr(
                "Completed and skipped tasks were removed; failed/cancelled tasks were kept",
                "已移除已完成/已跳过任务；失败/中断任务已保留",
                "完了/スキップのみ削除し、失敗/中断タスクは保持しました",
            ),
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=2500,
            parent=self,
        )

    def _cancel_all_active(self):
        count = download_manager.cancel_all_active()
        self._schedule_refresh(0)
        InfoBar.info(
            title=tr("Cancel Requested", "已请求中断", "中断要求済み"),
            content=tr(
                f"Requested cancellation for {count} tasks",
                f"已请求中断 {count} 个任务",
                f"{count} 件の中断を要求しました",
            ),
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=2500,
            parent=self,
        )

    def _retry_all_failed(self):
        retried, skipped = download_manager.retry_all_failed(
            exclude_downloaded=self._exclude_downloaded_switch.isChecked()
        )
        self._schedule_refresh(0)
        InfoBar.success(
            title=tr("Retry Triggered", "批量重试已触发", "再試行を開始"),
            content=tr(
                f"Retried {retried}, skipped-as-completed {skipped}",
                f"重试 {retried} 个，排除并标记完成 {skipped} 个",
                f"再試行 {retried} 件、除外して完了扱い {skipped} 件",
            ),
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=2800,
            parent=self,
        )

    def _restore_all_cancelled(self):
        restored = download_manager.restore_all_cancelled()
        self._schedule_refresh(0)
        InfoBar.success(
            title=tr("Restore Triggered", "批量恢复已触发", "復元を開始"),
            content=tr(
                f"Restored {restored} cancelled tasks",
                f"恢复 {restored} 个已中断任务",
                f"{restored} 件の中断タスクを復元しました",
            ),
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=2500,
            parent=self,
        )

    # ── Signal slots ──────────────────────────────────────────────────────────

    def _on_task_added(self, task_id: str, _info: dict):
        if task_id in self._tasks_by_id:
            return
        self._ensure_order(task_id)
        info = dict(_info or {})
        info["task_id"] = task_id
        task = self._fetch_task(task_id, info)
        if task:
            self._tasks_by_id[task_id] = task
            self._append_visible_tasks([task])

    def _on_tasks_added(self, infos: list):
        added: list[DownloadTask] = []
        for info in infos:
            task_id = str(info.get("task_id", "") or "")
            if task_id:
                self._ensure_order(task_id)
                if task_id in self._tasks_by_id:
                    continue
                task = self._fetch_task(task_id, info)
                if task:
                    self._tasks_by_id[task_id] = task
                    added.append(task)
        self._append_visible_tasks(added)

    def _on_task_status_changed(self, task_id: str, _status_str: str):
        self._ensure_order(task_id)
        task = self._fetch_task(task_id)
        if not task:
            return
        self._tasks_by_id[task_id] = task
        self._upsert_task_row(task)
        if self._sort_column in (self._SORT_DEFAULT, self._COL_STATE):
            self._schedule_refresh(300)

    def _on_task_priority_changed(self, task_id: str, _priority: int):
        task = self._fetch_task(task_id)
        if not task:
            return
        self._tasks_by_id[task_id] = task
        self._upsert_task_row(task)
        if self._sort_column in (self._SORT_DEFAULT, self._COL_PRIORITY):
            self._schedule_refresh(0)

    def _on_task_progress(self, task_id: str, _downloaded: int, _total: int, _speed: str):
        self._ensure_order(task_id)
        task = self._tasks_by_id.get(task_id)
        if task is None:
            task = self._fetch_task(task_id)
        if task:
            self._tasks_by_id[task_id] = task
            task.downloaded_bytes = _downloaded
            task.total_bytes = _total
            task.speed_str = _speed
        self._pending_progress_ids.add(task_id)
        if self._progress_flush_pending:
            return
        self._progress_flush_pending = True
        QTimer.singleShot(180, self._flush_progress_updates)

    def _on_task_error(self, task_id: str, _message: str):
        self._ensure_order(task_id)
        task = self._fetch_task(task_id)
        if task:
            self._tasks_by_id[task_id] = task
            self._upsert_task_row(task)

    def _on_task_removed(self, task_id: str):
        self._tasks_by_id.pop(task_id, None)
        self._remove_visible_task_ids([task_id])

    def _on_tasks_removed(self, task_ids: list):
        changed = False
        clean_ids: list[str] = []
        for raw_task_id in task_ids:
            task_id = str(raw_task_id or "")
            if not task_id:
                continue
            clean_ids.append(task_id)
            if self._tasks_by_id.pop(task_id, None) is not None:
                changed = True
        if changed:
            self._remove_visible_task_ids(clean_ids)

    def _flush_progress_updates(self):
        self._progress_flush_pending = False
        ids = list(self._pending_progress_ids)
        self._pending_progress_ids.clear()
        if self._sort_column in (self._COL_PROGRESS, self._COL_SIZE, self._COL_SPEED):
            self._schedule_refresh(120)
            return
        for task_id in ids:
            self._update_progress_cells(task_id)

    def _update_progress_cells(self, task_id: str):
        task = self._tasks_by_id.get(task_id)
        row = self._row_by_task_id.get(task_id)
        if not task or row is None or row < 0 or row >= self._table.rowCount():
            return
        updates = {
            self._COL_PROGRESS: self._progress_text(task),
            self._COL_SIZE: self._size_text(task),
            self._COL_SPEED: task.speed_str,
        }
        for column, value in updates.items():
            item = self._table.item(row, column)
            if not item:
                continue
            item.setText(value)
            item.setToolTip(self._cell_tooltip(task, column, value))

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _ensure_order(self, task_id: str):
        if task_id in self._task_order:
            return
        self._task_order[task_id] = self._next_order
        self._next_order += 1

    def _task_search_text(self, task: DownloadTask) -> str:
        return "\n".join(
            [
                status_label(task.status),
                task.title,
                task.author,
                task.username,
                task.video_id,
                _video_url(task.video_id),
                task.quality,
                task.error_msg,
                task.file_path,
            ]
        ).lower()

    def _cell_tooltip(self, task: DownloadTask, column: int, value: str) -> str:
        if column == self._COL_STATE and task.error_msg:
            return f"{value}\n{task.error_msg}"
        if column == self._COL_PROGRESS and task.error_msg:
            return task.error_msg
        return value

    @staticmethod
    def _priority_text(priority: int) -> str:
        if priority > 0:
            return tr("High", "高", "高")
        if priority < 0:
            return tr("Low", "低", "低")
        return tr("Normal", "普通", "通常")

    def _progress_ratio(self, task: DownloadTask) -> float:
        if task.status == TaskStatus.COMPLETED:
            return 1.0
        if task.total_bytes > 0:
            return max(0.0, min(1.0, task.downloaded_bytes / task.total_bytes))
        return 0.0

    def _progress_text(self, task: DownloadTask) -> str:
        if task.status == TaskStatus.COMPLETED:
            return "100%"
        if task.status == TaskStatus.RESOLVING:
            return tr("Resolving", "解析中", "解析中")
        if task.status == TaskStatus.QUEUED_META:
            return tr("Queued", "排队中", "待機中")
        if task.status == TaskStatus.QUEUED_DOWNLOAD:
            return tr("Waiting", "待下载", "待機")
        if task.status == TaskStatus.CANCELLING:
            return tr("Cancelling", "中断中", "中断中")
        if task.status == TaskStatus.FAILED and task.error_msg:
            return task.error_msg
        if task.total_bytes > 0:
            return f"{self._progress_ratio(task) * 100:.1f}%"
        return ""

    def _size_text(self, task: DownloadTask) -> str:
        if task.total_bytes > 0:
            return f"{_fmt_bytes(task.downloaded_bytes)} / {_fmt_bytes(task.total_bytes)}"
        if task.downloaded_bytes > 0:
            return _fmt_bytes(task.downloaded_bytes)
        return ""


def _fmt_bytes(n: int) -> str:
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.1f} GB"
    if n >= 1024 ** 2:
        return f"{n / 1024 ** 2:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n} B"


def _video_url(video_id: str) -> str:
    video_id = str(video_id or "").strip()
    return f"https://www.iwara.tv/video/{video_id}" if video_id else ""
