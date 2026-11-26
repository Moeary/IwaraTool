"""Add videos to the download workbench's waiting list from any page.

The pre-check (which may hit the site, e.g. for an author's whole list) runs
in the background; the videos then wait on the workbench for confirmation and
a notice offers to go there.  A lone downloadable video may start at once
(Settings: direct_download_single).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QWidget

from qfluentwidgets import InfoBar, InfoBarPosition, PushButton

from ...config import app_config
from ...core.rules import active_rule_id
from ...core.search.models import SearchVideo
from ...presentation.downloads.staging import (  # noqa: F401 - video_payload re-exported
    checking_text,
    review_label,
    stage,
    staging_notice,
    video_payload,
)
from ..common.worker_lifecycle import ManagedThread, on_finished, stop_qthreads
from .page_dependencies import download_manager


def go_to_workbench(host: QWidget):
    window = host.window()
    page = getattr(window, "_download_page", None)
    if page is not None and hasattr(window, "switchTo"):
        window.switchTo(page)


class StageWorker(ManagedThread):
    """Pre-check (``job`` returns a preview) and stage it, off the GUI thread."""

    result_ready = Signal(dict)

    def __init__(self, job: Callable[[], dict[str, Any]], direct_single: bool):
        super().__init__()
        self._job = job
        self._direct_single = direct_single

    def run(self):
        result = stage(download_manager, self._job, direct_single=self._direct_single)
        if not self.isInterruptionRequested():
            self.result_ready.emit(result)


class StagingFlow(QObject):
    """One pre-check at a time for ``host``, then a notice (with "Review") about the result."""

    busy_changed = Signal(bool)
    staged = Signal(dict)  # the stage_preview() result, once it is on the workbench

    def __init__(self, host: QWidget):
        super().__init__(host)
        self._host = host
        self._worker: StageWorker | None = None
        self._info_bar = None

    def busy(self) -> bool:
        return self._worker is not None

    @property
    def worker(self) -> StageWorker | None:
        """The running pre-check, for a page's shutdown list."""
        return self._worker

    def start(self, job: Callable[[], dict[str, Any]], *, busy_text: str = "", allow_direct: bool = True) -> bool:
        """Pre-check what ``job`` lists and add it to the waiting list; False if one is running."""

        if self._worker is not None:
            return False
        title, default_text = checking_text()
        self._info_bar = InfoBar.info(
            title=title,
            content=busy_text or default_text,
            orient=Qt.Orientation.Horizontal,
            isClosable=False,
            position=InfoBarPosition.TOP,
            duration=-1,
            parent=self._host,
        )
        worker = StageWorker(job, allow_direct and bool(app_config.direct_download_single))
        worker.result_ready.connect(self._on_ready)
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

    def _on_ready(self, result: dict[str, Any]):
        self._close_info_bar()
        notice = staging_notice(result)
        if not result.get("error") and not result.get("direct"):
            self.staged.emit(result)
        # The notice kinds ("info", "success", "warning", "error") are InfoBar's method names.
        self._notice(getattr(InfoBar, notice.kind), notice.title, notice.content, review=notice.review)

    def _notice(self, kind, title: str, content: str, *, review: bool = False):
        bar = kind(
            title=title, content=content, orient=Qt.Orientation.Horizontal, isClosable=True,
            position=InfoBarPosition.TOP, duration=6000 if review else 3500, parent=self._host,
        )
        if review and getattr(self._host.window(), "_download_page", None) is not self._host:
            button = PushButton(review_label(), bar)
            button.clicked.connect(lambda: (go_to_workbench(self._host), bar.close()))
            bar.addWidget(button)

    def shutdown(self, timeout_ms: int = 30_000) -> bool:
        return stop_qthreads([self._worker], timeout_ms=timeout_ms)


def flow_for(host: QWidget) -> StagingFlow:
    """The host's own flow, created the first time it is needed."""

    flow = getattr(host, "_staging_flow", None)
    if not isinstance(flow, StagingFlow):
        flow = StagingFlow(host)
        host._staging_flow = flow
    return flow


def stage_videos(host: QWidget, videos: list[SearchVideo], *, label: str, rule_id: str = "") -> bool:
    """Add videos a page already shows (home, author page, detail page) to the waiting list."""

    payloads = [video_payload(video) for video in videos]
    if not payloads:
        return False
    rule = rule_id or active_rule_id()
    return flow_for(host).start(lambda: download_manager.preview_videos(payloads, rule_id=rule, label=label))
