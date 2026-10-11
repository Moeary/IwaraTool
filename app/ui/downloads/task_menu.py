"""Row clicks, selection and the right-click menu of the task table."""

from __future__ import annotations

from PySide6.QtCore import Qt

from qfluentwidgets import Action, FluentIcon, InfoBar, InfoBarPosition, RoundMenu

from ...core.models import DownloadTask, TaskStatus
from ...i18n import tr
from .page_dependencies import download_manager
from .task_statuses import _ACTIVE_STATUSES, _TERMINAL_STATUSES
from .task_text import _video_url


class TaskMenuMixin:
    """Row clicks, selection and the right-click menu of the task table."""

    def _primary_action(self, task: DownloadTask):
        if task.status == TaskStatus.STAGED:
            return self._staged_primary_action(task)
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
        if self._run_staged_action(action, task_id):
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
        self._add_staged_actions(menu, grouped.get(TaskStatus.STAGED, []))
        if completed_ids:
            self._add_completed_task_actions(menu, completed_ids)
        if completed_ids and (
            failed_ids or cancelled_ids or interruptible_ids or cancelling_ids
        ):
            menu.addSeparator()
        self._add_task_state_actions(menu, failed_ids, cancelled_ids, interruptible_ids, cancelling_ids)
        self._add_explain_action(menu, task_ids, completed_ids)
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

    def _add_explain_action(self, menu: RoundMenu, task_ids: list[str], completed_ids: list[str]):
        """One task that has not finished: ask the workbench why."""

        if len(task_ids) != 1 or completed_ids:
            return
        task = self._tasks_by_id.get(task_ids[0])
        video_id = task.video_id if task else ""
        if not video_id:
            return
        menu.addAction(
            Action(
                FluentIcon.HELP,
                tr("Why not downloaded?", "为什么没下载？", "なぜ保存されない？"),
                self,
                triggered=lambda _checked=False, vid=video_id: self.explain_requested.emit(vid),
            )
        )

    def _add_completed_task_actions(self, menu: RoundMenu, completed_ids: list[str]):
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

    def _add_task_state_actions(self, menu: RoundMenu, failed_ids, cancelled_ids, interruptible_ids, cancelling_ids):
        """Retry / restore / interrupt entries for the selected tasks' states."""

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
