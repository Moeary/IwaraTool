"""Task card widget — one card per DownloadTask in the task list."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    CaptionLabel,
    IconWidget,
    ProgressBar,
    ToolButton,
    FluentIcon,
)

from ..core.models import DownloadTask, TaskStatus, STATUS_LABELS
from ..signal_bus import signal_bus
from ..core.manager import download_manager


# Colour map for status badges
_STATUS_COLORS: dict[TaskStatus, str] = {
    TaskStatus.QUEUED_META: "#888888",
    TaskStatus.RESOLVING: "#0078d4",
    TaskStatus.QUEUED_DOWNLOAD: "#8764b8",
    TaskStatus.DOWNLOADING: "#0f7b0f",
    TaskStatus.COMPLETED: "#107c10",
    TaskStatus.FAILED: "#c42b1c",
}


class TaskCard(CardWidget):
    """Fluent-style card that represents one download task."""

    def __init__(self, task: DownloadTask, parent: QWidget | None = None):
        super().__init__(parent)
        self.task_id = task.task_id
        self._build_ui()
        self._update_from_task(task)

        # Connect global signals
        signal_bus.task_status_changed.connect(self._on_status_changed)
        signal_bus.task_progress_updated.connect(self._on_progress)
        signal_bus.task_error.connect(self._on_error)

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        self.setFixedHeight(90)

        root = QHBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 8)
        root.setSpacing(12)

        # ── Left: thumbnail placeholder ──────────────────────────────────────
        self._thumb = IconWidget(FluentIcon.VIDEO, self)
        self._thumb.setFixedSize(54, 54)
        root.addWidget(self._thumb)

        # ── Center: title / author / status / progress ───────────────────────
        center = QVBoxLayout()
        center.setSpacing(2)

        title_row = QHBoxLayout()
        self._title_lbl = BodyLabel("", self)
        self._title_lbl.setMaximumWidth(400)
        self._status_lbl = CaptionLabel("", self)
        title_row.addWidget(self._title_lbl)
        title_row.addSpacing(8)
        title_row.addWidget(self._status_lbl)
        title_row.addStretch()

        self._author_lbl = CaptionLabel("", self)
        self._progress_lbl = CaptionLabel("", self)

        self._progress_bar = ProgressBar(self)
        self._progress_bar.setMinimum(0)
        self._progress_bar.setMaximum(1000)
        self._progress_bar.setValue(0)
        self._progress_bar.setFixedHeight(4)

        center.addLayout(title_row)
        center.addWidget(self._author_lbl)
        center.addWidget(self._progress_bar)
        center.addWidget(self._progress_lbl)
        root.addLayout(center, stretch=1)

        # ── Right: action button ──────────────────────────────────────────────
        self._action_btn = ToolButton(FluentIcon.DELETE, self)
        self._action_btn.setToolTip("移除任务")
        self._action_btn.clicked.connect(self._on_action)
        root.addWidget(self._action_btn, alignment=Qt.AlignmentFlag.AlignVCenter)

    # ── Data update ───────────────────────────────────────────────────────────

    def _update_from_task(self, task: DownloadTask):
        self._title_lbl.setText(task.title or task.video_id)
        self._author_lbl.setText(f"作者: {task.author}" if task.author else "")
        self._set_status(task.status)

        if task.status == TaskStatus.DOWNLOADING and task.total_bytes > 0:
            pct = task.downloaded_bytes / task.total_bytes
            self._progress_bar.setValue(int(pct * 1000))
            self._progress_lbl.setText(
                f"{_fmt_bytes(task.downloaded_bytes)} / {_fmt_bytes(task.total_bytes)}  {task.speed_str}"
            )
        elif task.status == TaskStatus.COMPLETED:
            self._progress_bar.setValue(1000)
            self._progress_lbl.setText("已完成")
        elif task.status == TaskStatus.FAILED:
            self._progress_bar.setValue(0)
            self._progress_lbl.setText(f"错误: {task.error_msg}")

    def _set_status(self, status: TaskStatus):
        label = STATUS_LABELS.get(status, status.value)
        color = _STATUS_COLORS.get(status, "#888888")
        self._status_lbl.setText(label)
        self._status_lbl.setStyleSheet(
            f"color: {color}; font-weight: bold;"
        )
        # Show retry button for failed tasks, delete button otherwise
        if status == TaskStatus.FAILED:
            self._action_btn.setIcon(FluentIcon.SYNC)
            self._action_btn.setToolTip("重试")
        else:
            self._action_btn.setIcon(FluentIcon.DELETE)
            self._action_btn.setToolTip("移除任务")

        # Indeterminate progress for resolving
        if status == TaskStatus.RESOLVING:
            self._progress_bar.setMinimum(0)
            self._progress_bar.setMaximum(0)  # indeterminate
        else:
            self._progress_bar.setMinimum(0)
            self._progress_bar.setMaximum(1000)

    # ── Slot handlers ─────────────────────────────────────────────────────────

    def _on_status_changed(self, task_id: str, status_str: str):
        if task_id != self.task_id:
            return
        try:
            status = TaskStatus(status_str)
        except ValueError:
            return
        self._set_status(status)
        if status == TaskStatus.COMPLETED:
            self._progress_bar.setValue(1000)
            self._progress_lbl.setText("已完成")
        elif status in (
            TaskStatus.QUEUED_META,
            TaskStatus.RESOLVING,
            TaskStatus.QUEUED_DOWNLOAD,
        ):
            self._progress_bar.setValue(0)
            self._progress_lbl.setText("")

    def _on_progress(self, task_id: str, downloaded: int, total: int, speed: str):
        if task_id != self.task_id:
            return
        if total > 0:
            self._progress_bar.setMaximum(1000)
            self._progress_bar.setValue(int(downloaded / total * 1000))
        self._progress_lbl.setText(
            f"{_fmt_bytes(downloaded)} / {_fmt_bytes(total)}  {speed}".strip()
        )

    def _on_error(self, task_id: str, msg: str):
        if task_id != self.task_id:
            return
        self._progress_lbl.setText(f"错误: {msg}")

    def _on_action(self):
        task = next(
            (t for t in download_manager.get_tasks() if t.task_id == self.task_id),
            None,
        )
        if task and task.status == TaskStatus.FAILED:
            download_manager.retry_task(self.task_id)
        else:
            download_manager.remove_task(self.task_id)
            self.setParent(None)
            self.deleteLater()


# ── Utility ──────────────────────────────────────────────────────────────────

def _fmt_bytes(n: int) -> str:
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.1f} GB"
    if n >= 1024 ** 2:
        return f"{n / 1024 ** 2:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n} B"
