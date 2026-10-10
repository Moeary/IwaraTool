"""Look before queueing: classify in the background, confirm in a dialog, then queue.

Shared by the download workbench (author / playlist links), the search page
and the subscription page, so every way of adding downloads first shows how
many videos there are, how many are already here, what the rule keeps, where
the files go and what they will be called.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QWidget

from qfluentwidgets import InfoBar, InfoBarPosition

from ...config import app_config
from ...i18n import tr
from ..worker_lifecycle import ManagedThread, on_finished, stop_qthreads
from .listing_preview_dialog import ListingPreviewDialog


def submissions_mark_only() -> bool:
    """Submitted videos are only recorded as downloaded, not fetched (Settings)."""

    return bool(app_config.mark_submitted_as_downloaded or not app_config.download_video_file)


class PreviewWorker(ManagedThread):
    """Run one preview job (it may hit the network) off the GUI thread."""

    result_ready = Signal(dict)

    def __init__(self, job: Callable[[], dict[str, Any]]):
        super().__init__()
        self._job = job

    def run(self):
        try:
            result = self._job()
        except Exception as exc:  # a failed lookup must still end the busy state
            result = {"error": str(exc), "items": []}
        if not self.isInterruptionRequested():
            self.result_ready.emit(result)


class QueuePreviewFlow(QObject):
    """One preview at a time for ``host``: busy notice, dialog, then ``on_chosen``."""

    busy_changed = Signal(bool)

    def __init__(self, host: QWidget):
        super().__init__(host)
        self._host = host
        self._worker: PreviewWorker | None = None
        self._info_bar = None

    def busy(self) -> bool:
        return self._worker is not None

    @property
    def worker(self) -> PreviewWorker | None:
        """The running lookup, for a page's shutdown list."""
        return self._worker

    def start(
        self,
        job: Callable[[], dict[str, Any]],
        on_chosen: Callable[[dict[str, Any], list[dict[str, Any]]], None],
        *,
        mark_only: bool = False,
        busy_text: str = "",
    ) -> bool:
        """Run ``job`` (returns a preview dict); False if a preview is already running."""

        if self._worker is not None:
            return False
        self._info_bar = InfoBar.info(
            title=tr("Checking", "正在检查", "確認中"),
            content=busy_text or tr(
                "Listing the videos, what is already here and what the rule keeps…",
                "正在列出作品，并核对已有项与下载规则…",
                "動画・保存済み・ルールを確認しています…",
            ),
            orient=Qt.Orientation.Horizontal,
            isClosable=False,
            position=InfoBarPosition.TOP,
            duration=-1,
            parent=self._host,
        )
        worker = PreviewWorker(job)
        worker.result_ready.connect(
            lambda preview: self._on_ready(preview, on_chosen, mark_only=mark_only)
        )
        on_finished(worker, self._host, self._on_finished)
        self._worker = worker
        self.busy_changed.emit(True)
        worker.start()
        return True

    def _on_finished(self, worker):
        if worker is self._worker:
            self._worker = None
            self._close_info_bar()
            self.busy_changed.emit(False)
        worker.deleteLater()

    def _close_info_bar(self):
        info_bar, self._info_bar = self._info_bar, None
        if info_bar is not None:
            try:
                info_bar.close()
            except RuntimeError:
                pass  # already gone

    def _on_ready(self, preview: dict[str, Any], on_chosen, *, mark_only: bool):
        self._close_info_bar()
        error = str(preview.get("error", "") or "")
        if error:
            InfoBar.error(
                title=tr("Failed", "失败", "失敗"),
                content=error,
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=6000,
                parent=self._host,
            )
            return
        chosen = ListingPreviewDialog(preview, mark_only=mark_only, parent=self._host.window()).choose()
        if chosen:
            on_chosen(preview, chosen)

    def shutdown(self, timeout_ms: int = 30_000) -> bool:
        return stop_qthreads([self._worker], timeout_ms=timeout_ms)
