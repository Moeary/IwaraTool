"""Retry, cancel, restore, remove and open tasks, one or many at a time."""

from __future__ import annotations

import webbrowser

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTableWidgetItem

from qfluentwidgets import InfoBar, InfoBarPosition

from ...core.models import TaskStatus
from ...i18n import tr
from ...signal_bus import signal_bus
from .page_dependencies import download_manager
from .task_statuses import _ACTIVE_STATUSES
from .task_text import _video_url


class TaskActionsMixin:
    """Retry, cancel, restore, remove and open tasks, one or many at a time."""

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
        if not task:
            return
        if task.status == TaskStatus.COMPLETED:
            self._open_task(task_id)
        elif task.video_id:
            # Not on disk yet: stream it in the built-in player.
            signal_bus.video_preview_requested.emit(task.video_id, task.title or "", "")

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
