"""The workbench's "Pending" card: download confirmations that were put off."""

from __future__ import annotations

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import QAbstractItemView, QHBoxLayout, QListWidgetItem, QVBoxLayout, QWidget

from qfluentwidgets import CardWidget, FluentIcon, ListWidget, PushButton, SubtitleLabel, TransparentToolButton

from ...core.models import TaskStatus
from ...i18n import tr
from ...signal_bus import signal_bus
from ..fluent_dialogs import show_fluent_confirmation
from ..theme import CARD_MARGINS
from .page_dependencies import download_manager


class PendingStacksCard(CardWidget):
    """One row per put-off confirmation; open it again, drop it, or drop them all.

    A stack leaves by itself once every video it would download is in the
    history; the card hides when nothing is pending.
    """

    open_requested = Signal(str)  # stack id

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*CARD_MARGINS)
        layout.setSpacing(8)
        header = QHBoxLayout()
        self._title = SubtitleLabel("", self)
        header.addWidget(self._title)
        header.addStretch(1)
        clear_btn = TransparentToolButton(FluentIcon.DELETE, self)
        clear_btn.setToolTip(tr("Clear every pending batch", "清空全部待下载", "保留をすべて削除"))
        clear_btn.clicked.connect(self._clear_all)
        header.addWidget(clear_btn)
        layout.addLayout(header)

        self._list = ListWidget(self)
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.setMaximumHeight(150)
        self._list.itemDoubleClicked.connect(lambda item: self._open(item))
        layout.addWidget(self._list)

        buttons = QHBoxLayout()
        open_btn = PushButton(tr("Open", "打开下载弹窗", "開く"), self, FluentIcon.DOWNLOAD)
        open_btn.clicked.connect(lambda: self._open(self._list.currentItem()))
        remove_btn = PushButton(tr("Remove", "移除此批", "削除"), self, FluentIcon.REMOVE)
        remove_btn.clicked.connect(self._remove_current)
        buttons.addWidget(open_btn, 1)
        buttons.addWidget(remove_btn)
        layout.addLayout(buttons)

        self._prune_timer = QTimer(self)
        self._prune_timer.setSingleShot(True)
        self._prune_timer.setInterval(1500)
        self._prune_timer.timeout.connect(download_manager.prune_pending_stacks)
        signal_bus.pending_stacks_changed.connect(self.refresh)
        signal_bus.history_changed.connect(self._prune_timer.start)
        signal_bus.task_status_changed.connect(self._on_task_status)
        self.refresh()
        self._prune_timer.start()

    def refresh(self):
        stacks = download_manager.list_pending_stacks()
        current = self._list.currentItem()
        current_id = str(current.data(Qt.ItemDataRole.UserRole) or "") if current is not None else ""
        self._list.clear()
        for stack in stacks:
            label = stack["label"] or tr("Videos", "作品", "動画")
            item = QListWidgetItem(tr(
                f"{label}  ·  {stack['wanted']} to download / {stack['total']}  ·  {stack['created_at']}",
                f"{label}  ·  待下载 {stack['wanted']} / 共 {stack['total']}  ·  {stack['created_at']}",
                f"{label}  ·  保存予定 {stack['wanted']} / {stack['total']}  ·  {stack['created_at']}",
            ))
            item.setData(Qt.ItemDataRole.UserRole, stack["id"])
            item.setToolTip(tr("Double-click to open the download dialog", "双击重新打开下载弹窗", "ダブルクリックで開く"))
            self._list.addItem(item)
            if stack["id"] == current_id:
                self._list.setCurrentItem(item)
        if self._list.currentItem() is None and self._list.count():
            self._list.setCurrentRow(0)
        self._title.setText(tr(f"Pending ({len(stacks)})", f"待下载（{len(stacks)}）", f"保留中（{len(stacks)}）"))
        self.setVisible(bool(stacks))

    def _on_task_status(self, _task_id: str, status: str):
        if status == TaskStatus.COMPLETED.value:
            self._prune_timer.start()

    def _open(self, item: QListWidgetItem | None):
        if item is not None:
            self.open_requested.emit(str(item.data(Qt.ItemDataRole.UserRole) or ""))

    def _remove_current(self):
        item = self._list.currentItem()
        if item is not None:
            download_manager.remove_pending_stack(str(item.data(Qt.ItemDataRole.UserRole) or ""))

    def _clear_all(self):
        if show_fluent_confirmation(
            self.window(),
            tr("Clear pending downloads", "清空待下载", "保留を削除"),
            tr(
                "Remove every pending batch? Nothing on disk or in the queue is touched.",
                "移除全部待下载批次？不会影响已下载的文件和任务队列。",
                "保留中の一覧をすべて削除しますか？ファイルとキューはそのままです。",
            ),
        ):
            download_manager.clear_pending_stacks()
