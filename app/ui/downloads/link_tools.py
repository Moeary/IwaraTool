"""The workbench's link preview (author / playlist) and its "why not downloaded" answer."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt

from qfluentwidgets import InfoBar, InfoBarPosition

from ...i18n import tr
from ..worker_lifecycle import on_finished
from .diagnosis_dialog import DiagnosisDialog
from .link_workers import DiagnosisWorker
from .page_dependencies import download_manager
from .queue_preview import QueuePreviewFlow
from .staging_flow import StagingFlow
from .submitted_urls import split_submitted_urls


class DownloadLinkToolsMixin:
    """The workbench's link preview (author / playlist) and its "why not downloaded" answer."""

    def _init_link_tools(self):
        self._diagnosis_worker: DiagnosisWorker | None = None
        self._preview_flow = QueuePreviewFlow(self)  # re-opens pending batches from before
        self._staging_flow = StagingFlow(self)
        self._staging_flow.busy_changed.connect(lambda busy: self._submit_btn.setEnabled(not busy))

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

    def _start_listing_preview(self, url: str, rule_id: str) -> bool:
        """List what ``url`` holds in the background; the videos then wait for confirmation here."""

        def staged(result: dict[str, Any]):
            if result.get("staged") or result.get("direct"):
                self._maybe_add_download_source_to_subscription(url)
                if self._url_edit.text().strip() == url:
                    self._url_edit.clear()

        flow = self._staging_flow
        try:
            flow.staged.disconnect()
        except (RuntimeError, TypeError):
            pass  # nothing connected yet
        flow.staged.connect(staged)
        return flow.start(
            lambda: download_manager.preview_listing(url, rule_id=rule_id),
            busy_text=tr("Listing every video behind the link…", "正在列出链接中的全部作品并预解析…", "リンク内の動画を一覧にしています…"),
            allow_direct=False,
        )
