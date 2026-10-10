"""Text shown for a task: sizes, progress, priority, tooltips and search text."""

from __future__ import annotations

from ...core.models import DownloadTask, TaskStatus, status_label
from ...i18n import tr


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


class TaskTextMixin:
    """Text shown for a task: sizes, progress, priority, tooltips and search text."""

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
