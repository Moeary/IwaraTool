"""The workbench's link preview (author / playlist) and its "why not downloaded" answer."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt

from qfluentwidgets import InfoBar, InfoBarPosition

from ...i18n import tr
from ...signal_bus import signal_bus
from ..worker_lifecycle import on_finished
from .diagnosis_dialog import DiagnosisDialog
from .link_workers import DiagnosisWorker
from .page_dependencies import download_manager
from .queue_preview import QueuePreviewFlow
from .submitted_urls import split_submitted_urls


def queue_chosen(host, preview: dict[str, Any], chosen: list[dict[str, Any]], *, mark_only: bool, source_label: str = ""):
    """Queue (or only mark) the videos picked in a preview dialog and say so on ``host``."""

    if mark_only:
        marked = download_manager.mark_video_items_downloaded([item["raw"] for item in chosen])
        signal_bus.history_changed.emit()
        message = tr(f"Marked {marked} as downloaded", f"已标记 {marked} 个为已下载", f"{marked} 件を保存済みにしました")
    else:
        queued = download_manager.enqueue_video_ids(
            [str(item["video_id"]) for item in chosen],
            source_label=source_label or str(preview.get("label", "") or ""),
            rule_id=str(preview.get("rule_id", "") or ""),
        )
        message = tr(f"Queued {queued} videos", f"已加入队列 {queued} 个作品", f"{queued} 件をキューに追加しました")
    InfoBar.success(
        title=tr("Submitted", "已提交", "送信しました"),
        content=message,
        orient=Qt.Orientation.Horizontal,
        isClosable=True,
        position=InfoBarPosition.TOP,
        duration=3000,
        parent=host,
    )


class DownloadLinkToolsMixin:
    """The workbench's link preview (author / playlist) and its "why not downloaded" answer."""

    def _init_link_tools(self):
        self._diagnosis_worker: DiagnosisWorker | None = None
        self._preview_flow = QueuePreviewFlow(self)
        self._preview_flow.busy_changed.connect(lambda busy: self._submit_btn.setEnabled(not busy))

    # ── why not downloaded ─────────────────────────────────────────────────

    def _explain_current_url(self):
        urls = split_submitted_urls(self._url_edit.text())
        if not urls:
            InfoBar.warning(
                title=tr("Notice", "提示", "お知らせ"),
                content=tr("Paste a video link or ID first", "请先粘贴视频链接或 ID", "先に動画のリンクまたは ID を貼り付けてください"),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=4000,
                parent=self,
            )
            return
        self.explain_video(urls[0])

    def explain_video(self, text: str):
        """Look up why ``text`` (a video link or id) was not downloaded, then show the answer."""

        if self._diagnosis_worker is not None:
            return
        self._why_btn.setEnabled(False)
        worker = DiagnosisWorker(str(text or ""), self._rule_picker.selected_rule_id())
        worker.result_ready.connect(self._on_diagnosis_ready)
        on_finished(worker, self, self._on_diagnosis_finished)
        self._diagnosis_worker = worker
        worker.start()

    def _on_diagnosis_finished(self, worker):
        if worker is self._diagnosis_worker:
            self._diagnosis_worker = None
            self._why_btn.setEnabled(True)
        worker.deleteLater()

    def _on_diagnosis_ready(self, result: dict[str, Any]):
        if not DiagnosisDialog(result, self.window()).ask():
            return
        video_id = str(result.get("video_id", "") or "")
        if video_id:
            download_manager.add_url(
                f"https://www.iwara.tv/video/{video_id}", rule_id=self._rule_picker.selected_rule_id(),
            )

    # ── author / playlist preview ───────────────────────────────────────────

    def _start_listing_preview(self, url: str, rule_id: str, *, mark_only: bool) -> bool:
        """Fetch what ``url`` contains in the background; False if a preview is already running."""

        def chosen(preview: dict[str, Any], items: list[dict[str, Any]]):
            self._maybe_add_download_source_to_subscription(url)
            queue_chosen(self, preview, items, mark_only=mark_only)
            if self._url_edit.text().strip() == url:
                self._url_edit.clear()

        return self._preview_flow.start(
            lambda: download_manager.preview_listing(url, rule_id=rule_id),
            chosen,
            mark_only=mark_only,
            busy_text=tr("Listing every video behind the link…", "正在列出链接中的全部作品…", "リンク内の動画を一覧にしています…"),
        )
