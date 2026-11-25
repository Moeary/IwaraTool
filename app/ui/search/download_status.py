"""Batched local download-state checks for the search interface."""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import QTimer, Signal

from ...core.search.models import IWARA_IMAGE_SOURCE_KIND, SearchVideo
from ...i18n import tr
from ...presentation.search.download_status import (  # noqa: F401  (re-exported for the page and tests)
    SearchDownloadStatus,
    download_status_text,
    iwara_history_id,
    query_download_statuses,
)
from ..common.worker_lifecycle import ManagedThread


class SearchDownloadStatusWorker(ManagedThread):
    """Keep SQLite and file checks off the GUI thread."""

    result_ready = Signal(object)

    def __init__(self, history: Any, video_ids: list[str], generation: int, revision: int):
        super().__init__()
        self.history = history
        self.video_ids = list(video_ids)
        self.generation = generation
        self.revision = revision

    def run(self):
        if self.isInterruptionRequested():
            return
        error = ""
        try:
            statuses = query_download_statuses(
                self.history, self.video_ids, interrupted=self.isInterruptionRequested
            )
        except Exception as exc:
            # A failed history lookup must not claim that a video was never
            # downloaded. Retry on the next explicit history/show refresh.
            statuses = {video_id: SearchDownloadStatus.UNIDENTIFIED for video_id in self.video_ids}
            error = str(exc)
        if not self.isInterruptionRequested():
            self.result_ready.emit(
                {
                    "generation": self.generation,
                    "revision": self.revision,
                    "statuses": statuses,
                    "error": error,
                }
            )


class SearchDownloadStatusMixin:
    """Coalesce page events into one query for the currently visible IDs."""

    def _init_download_status(self, history: Any, signal_bus: Any):
        self._download_status_history = history
        self._download_status_signal_bus = signal_bus
        self._download_status_by_id: dict[str, SearchDownloadStatus] = {}
        self._download_status_worker: SearchDownloadStatusWorker | None = None
        self._download_status_revision = 0
        self._download_status_dirty = False
        self._download_status_shutting_down = False
        self._download_status_refresh_timer = QTimer(self)
        self._download_status_refresh_timer.setSingleShot(True)
        self._download_status_refresh_timer.setInterval(120)
        self._download_status_refresh_timer.timeout.connect(self._start_download_status_refresh)
        signal_bus.history_changed.connect(self._on_download_history_changed)
        signal_bus.task_status_changed.connect(self._on_download_task_status_changed)

    def _download_status_text(self, video: SearchVideo) -> str:
        if video.source_kind == IWARA_IMAGE_SOURCE_KIND:
            return tr("Image post · browse only", "图片作品 · 仅浏览", "画像投稿 · 閲覧のみ")
        status = self._download_status_by_id.get(
            iwara_history_id(video), SearchDownloadStatus.UNIDENTIFIED
        )
        return download_status_text(status)

    def _request_download_status_refresh(self, *, force: bool = False):
        if self._download_status_shutting_down:
            return
        if force:
            self._download_status_dirty = True
            self._download_status_revision += 1
        if self._all_videos and self.isVisible() and not self._download_status_refresh_timer.isActive():
            self._download_status_refresh_timer.start()

    def _reset_download_statuses(self):
        self._download_status_refresh_timer.stop()
        self._download_status_by_id.clear()
        self._download_status_dirty = True
        self._download_status_revision += 1
        if self._download_status_worker is not None:
            self._download_status_worker.requestInterruption()

    def _stop_download_status_refresh(self):
        if self._download_status_shutting_down:
            return
        self._download_status_shutting_down = True
        self._download_status_refresh_timer.stop()
        self._download_status_signal_bus.history_changed.disconnect(self._on_download_history_changed)
        self._download_status_signal_bus.task_status_changed.disconnect(self._on_download_task_status_changed)

    def _on_download_history_changed(self):
        self._request_download_status_refresh(force=True)

    def _on_download_task_status_changed(self, _task_id: str, status: str):
        # Progress and intermediate task states do not change local history.
        if status == "completed":
            self._request_download_status_refresh(force=True)

    def _start_download_status_refresh(self):
        if self._download_status_shutting_down or not self.isVisible():
            return
        if self._download_status_worker is not None:
            return
        ids = list(dict.fromkeys(
            video_id for video in self._all_videos if (video_id := iwara_history_id(video))
        ))
        current_ids = set(ids)
        self._download_status_by_id = {
            video_id: status for video_id, status in self._download_status_by_id.items() if video_id in current_ids
        }
        pending_ids = ids if self._download_status_dirty else [
            video_id for video_id in ids if video_id not in self._download_status_by_id
        ]
        self._download_status_dirty = False
        if not pending_ids:
            return
        worker = SearchDownloadStatusWorker(
            self._download_status_history, pending_ids, self._generation, self._download_status_revision
        )
        self._download_status_worker = worker
        worker.result_ready.connect(self._on_download_status_result)
        worker.finished.connect(self._on_download_status_worker_finished)
        worker.start()

    def _on_download_status_result(self, result: dict[str, Any]):
        if (
            self._download_status_shutting_down
            or result.get("generation") != self._generation
            or result.get("revision") != self._download_status_revision
        ):
            return
        statuses = result.get("statuses") or {}
        self._download_status_by_id.update(statuses)
        self._update_download_status_presentation(set(statuses))
        error = str(result.get("error") or "")
        if error:
            self._download_status_signal_bus.log_message.emit(
                tr(
                    f"[Search] Could not check local download status: {error}",
                    f"[搜索] 无法检查本地下载状态：{error}",
                    f"[検索] ローカル保存状態を確認できませんでした：{error}",
                )
            )

    def _on_download_status_worker_finished(self):
        worker = self.sender()
        if worker is self._download_status_worker:
            self._download_status_worker = None
        worker.deleteLater()
        # IDs may have been resolved or history invalidated during the query.
        self._request_download_status_refresh()
