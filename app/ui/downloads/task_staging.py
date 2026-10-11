"""How the task list shows and acts on videos waiting for confirmation (STAGED)."""

from __future__ import annotations

import os

from PySide6.QtGui import QColor

from qfluentwidgets import Action, FluentIcon, RoundMenu

from ...config import app_config
from ...core.download.staging import STAGE_READY, STAGE_SKIP, STAGE_UNAVAILABLE
from ...core.models import DownloadTask, TaskStatus
from ...i18n import tr
from ..theme import qcolor
from .page_dependencies import download_manager


STATE_FILTER_STAGED = 7  # index of "To confirm" in the state filter


def staged_state_text(task: DownloadTask) -> str:
    if task.stage_verdict == STAGE_READY:
        return tr("Ready", "就绪", "保存可")
    if task.stage_verdict == STAGE_UNAVAILABLE:
        return tr("N/A", "不可", "不可")
    return tr("Skipped", "跳过", "スキップ")


def staged_state_color(task: DownloadTask) -> QColor:
    return qcolor({STAGE_READY: "success", STAGE_UNAVAILABLE: "danger"}.get(task.stage_verdict, "warning"))


def staged_detail(task: DownloadTask) -> str:
    """Why it is skipped / not downloadable, or where a ready video will be saved."""

    if task.stage_verdict != STAGE_READY or task.stage_reason:
        return task.stage_reason or ""
    if task.stage_path:
        return tr(f"→ {task.stage_path}", f"→ {task.stage_path}", f"→ {task.stage_path}")
    return ""


class TaskStagingMixin:
    """How the task list shows and acts on videos waiting for confirmation (STAGED)."""

    def _init_stage_filters(self):
        self._stage_verdict_filter = ""  # "" or STAGE_*
        self._stage_batch_filter = ""  # "" or a batch id

    def show_staged(self, verdict: str = "", batch_id: str = ""):
        """Show only the waiting videos (of one verdict / one batch)."""

        self._stage_verdict_filter = verdict
        self._stage_batch_filter = batch_id
        if self._state_combo.currentIndex() != STATE_FILTER_STAGED:
            self._state_combo.setCurrentIndex(STATE_FILTER_STAGED)  # re-filters
        else:
            self._apply_filters()

    def _passes_stage_filters(self, task: DownloadTask) -> bool:
        if task.status != TaskStatus.STAGED:
            return False
        if self._stage_verdict_filter and task.stage_verdict != self._stage_verdict_filter:
            return False
        return not self._stage_batch_filter or task.batch_id == self._stage_batch_filter

    @staticmethod
    def _stage_rank(task: DownloadTask) -> int:
        """Ready videos first, then skipped, then the ones that cannot download."""

        if task.status != TaskStatus.STAGED:
            return 0
        return {STAGE_READY: 0, STAGE_SKIP: 1, STAGE_UNAVAILABLE: 2}.get(task.stage_verdict, 1)

    def _staged_tooltip(self, task: DownloadTask) -> str:
        lines = [tr(f"Batch: {task.batch_label}", f"批次：{task.batch_label}", f"バッチ: {task.batch_label}")]
        if task.stage_reason:
            lines.append(task.stage_reason)
        if task.stage_path:
            lines.append(os.path.join(app_config.download_dir, task.stage_path))
        return "\n".join(lines)

    def _staged_primary_action(self, task: DownloadTask):
        if task.stage_verdict == STAGE_READY:
            return ("confirm", tr("Confirm", "确认", "確認"), tr("Download this video now", "确认下载这一个", "この動画を保存"), True)
        if task.stage_verdict == STAGE_SKIP:
            return ("force", tr("Allow", "仍下载", "許可"), tr("Download it anyway", "忽略跳过原因，仍然下载", "それでも保存"), True)
        return ("", tr("None", "无", "なし"), task.stage_reason, False)

    def _run_staged_action(self, action: str, task_id: str) -> bool:
        if action == "confirm":
            download_manager.confirm_staged(task_ids=[task_id])
        elif action == "force":
            download_manager.force_staged_ready([task_id])
            download_manager.confirm_staged(task_ids=[task_id])
        else:
            return False
        return True

    def _add_staged_actions(self, menu: RoundMenu, staged_ids: list[str]):
        if not staged_ids:
            return
        count = len(staged_ids)
        menu.addAction(Action(
            FluentIcon.DOWNLOAD,
            tr(f"Confirm download ({count})", f"确认下载所选（{count}）", f"選択を保存（{count}）"),
            self,
            triggered=lambda _checked=False, ids=tuple(staged_ids): download_manager.confirm_staged(task_ids=list(ids)),
        ))
        menu.addAction(Action(
            FluentIcon.ACCEPT,
            tr("Allow skipped ones anyway", "放行所选的跳过项", "スキップ分を許可"),
            self,
            triggered=lambda _checked=False, ids=tuple(staged_ids): download_manager.force_staged_ready(list(ids)),
        ))
        menu.addAction(Action(
            FluentIcon.REMOVE,
            tr(f"Remove from the waiting list ({count})", f"从待确认中移除（{count}）", f"確認待ちから削除（{count}）"),
            self,
            triggered=lambda _checked=False, ids=tuple(staged_ids): download_manager.discard_staged(task_ids=list(ids)),
        ))
        menu.addSeparator()
