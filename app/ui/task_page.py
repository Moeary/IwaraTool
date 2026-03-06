"""Task Center Interface — three-column task board."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from shiboken6 import isValid

from qfluentwidgets import (
    BodyLabel,
    FluentIcon,
    InfoBar,
    InfoBarPosition,
    PrimaryPushButton,
    ScrollArea,
    SwitchButton,
    SubtitleLabel,
    TitleLabel,
)

from ..core.manager import download_manager
from ..core.models import DownloadTask, TaskStatus
from ..i18n import tr
from ..signal_bus import signal_bus
from .task_card import TaskCard


# ── Scrollable list of task cards ─────────────────────────────────────────────

class TaskListWidget(QWidget):
    """Scrollable area that owns TaskCard children for a given set of statuses."""

    def __init__(self, filter_statuses: frozenset[TaskStatus], parent: QWidget | None = None):
        super().__init__(parent)
        self._filter = filter_statuses
        self._cards: dict[str, TaskCard] = {}  # task_id → card

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._scroll = ScrollArea(self)
        self._scroll.setWidgetResizable(True)
        outer.addWidget(self._scroll)

        self._container = QWidget()
        self._container.setObjectName("taskListContainer")
        self._v_layout = QVBoxLayout(self._container)
        self._v_layout.setContentsMargins(0, 0, 0, 0)
        self._v_layout.setSpacing(6)
        self._v_layout.addStretch()
        self._scroll.setWidget(self._container)

        self._empty_lbl = BodyLabel("暂无任务", self._container)
        self._empty_lbl.setText(tr("No tasks", "暂无任务"))
        self._empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._v_layout.insertWidget(0, self._empty_lbl)

    def add_card(self, task: DownloadTask):
        if task.task_id in self._cards:
            return
        card = TaskCard(task, self._container)
        self._cards[task.task_id] = card
        # Insert before the stretch
        idx = self._v_layout.count() - 1
        self._v_layout.insertWidget(idx, card)
        self._empty_lbl.setVisible(False)

    def remove_card(self, task_id: str):
        card = self._cards.pop(task_id, None)
        if card and isValid(card):
            self._v_layout.removeWidget(card)
            card.setParent(None)
            card.deleteLater()
        self._empty_lbl.setVisible(len(self._cards) == 0)

    def contains(self, task_id: str) -> bool:
        return task_id in self._cards

    def count(self) -> int:
        return len(self._cards)


class TaskCenterInterface(QWidget):
    """Page showing all download tasks in three side-by-side columns."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("TaskCenterInterface")
        self._build_ui()
        self._connect_signals()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(36, 24, 36, 16)
        root.setSpacing(12)

        # Title row
        title_row = QHBoxLayout()
        title_row.addWidget(TitleLabel(tr("Task Center", "任务中心"), self))
        title_row.addStretch()

        self._exclude_downloaded_switch = SwitchButton(self)
        self._exclude_downloaded_switch.setChecked(True)
        title_row.addWidget(BodyLabel("排除已下载", self))
        title_row.addWidget(self._exclude_downloaded_switch)

        retry_all_btn = PrimaryPushButton("全部重试", self, FluentIcon.SYNC)
        retry_all_btn.clicked.connect(self._retry_all_failed)
        title_row.addWidget(retry_all_btn)

        clear_btn = PrimaryPushButton(tr("Clear Completed", "清除已完成"), self, FluentIcon.BROOM)
        clear_btn.clicked.connect(self._clear_done)
        title_row.addWidget(clear_btn)
        root.addLayout(title_row)

        # Three columns displayed simultaneously
        board = QHBoxLayout()
        board.setSpacing(12)

        self._queued_list = TaskListWidget(
            frozenset([TaskStatus.QUEUED_META]), self
        )
        self._active_list = TaskListWidget(
            frozenset([TaskStatus.RESOLVING, TaskStatus.QUEUED_DOWNLOAD, TaskStatus.DOWNLOADING]),
            self,
        )
        self._done_list = TaskListWidget(
            frozenset([TaskStatus.COMPLETED, TaskStatus.FAILED]), self
        )

        board.addLayout(self._build_column(tr("Queued", "排队中"), self._queued_list), stretch=1)
        board.addLayout(self._build_column(tr("Downloading", "下载中"), self._active_list), stretch=1)
        board.addLayout(self._build_column(tr("Done / Failed", "已完成 / 失败"), self._done_list), stretch=1)

        root.addLayout(board, stretch=1)

    def _build_column(self, title: str, list_widget: TaskListWidget) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setSpacing(8)
        header = SubtitleLabel(title, self)
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col.addWidget(header)
        col.addWidget(list_widget, stretch=1)
        return col

    # ── Signal connections ────────────────────────────────────────────────────

    def _connect_signals(self):
        signal_bus.task_added.connect(self._on_task_added)
        signal_bus.task_status_changed.connect(self._on_status_changed)
        signal_bus.task_removed.connect(self._on_task_removed)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _list_for_status(self, status: TaskStatus) -> TaskListWidget | None:
        if status == TaskStatus.QUEUED_META:
            return self._queued_list
        if status in (TaskStatus.RESOLVING, TaskStatus.QUEUED_DOWNLOAD, TaskStatus.DOWNLOADING):
            return self._active_list
        if status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
            return self._done_list
        return None

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_task_added(self, task_id: str, info: dict):
        # Build a minimal DownloadTask-like object for the card
        from ..core.models import DownloadTask

        task = DownloadTask(
            task_id=task_id,
            url=info.get("url", ""),
            video_id=info.get("video_id", ""),
            title=info.get("title", ""),
            author=info.get("author", ""),
            status=TaskStatus.QUEUED_META,
        )
        self._queued_list.add_card(task)

    def _on_status_changed(self, task_id: str, status_str: str):
        try:
            new_status = TaskStatus(status_str)
        except ValueError:
            return

        new_list = self._list_for_status(new_status)

        # Move card between lists if needed
        for lst in (self._queued_list, self._active_list, self._done_list):
            if lst.contains(task_id):
                if lst is new_list:
                    return  # Already in the right list
                # Move: remove from old, add to new
                card_task = next(
                    (t for t in download_manager.get_tasks() if t.task_id == task_id),
                    None,
                )
                lst.remove_card(task_id)
                if new_list and card_task:
                    new_list.add_card(card_task)
                return

        # Card not found in any list yet — add to the correct one
        if new_list:
            task = next(
                (t for t in download_manager.get_tasks() if t.task_id == task_id),
                None,
            )
            if task:
                new_list.add_card(task)

    def _clear_done(self):
        download_manager.clear_completed()
        InfoBar.success(
            title=tr("Cleared", "已清除"),
            content=tr("All completed/failed tasks were removed", "已移除所有已完成和失败的任务"),
            orient=0,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=2500,
            parent=self,
        )

    def _on_task_removed(self, task_id: str):
        for lst in (self._queued_list, self._active_list, self._done_list):
            if lst.contains(task_id):
                lst.remove_card(task_id)

    def _retry_all_failed(self):
        retried, skipped = download_manager.retry_all_failed(
            exclude_downloaded=self._exclude_downloaded_switch.isChecked()
        )
        InfoBar.success(
            title=tr("Retry Triggered", "批量重试已触发"),
            content=f"重试 {retried} 个，排除并标记完成 {skipped} 个",
            orient=0,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=2800,
            parent=self,
        )
