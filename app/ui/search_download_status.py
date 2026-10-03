"""Batched local download-state checks for the search interface."""
from __future__ import annotations

import os
from enum import Enum
from typing import Any
from urllib.parse import unquote, urlsplit

from PySide6.QtCore import QThread, QTimer, Signal

from ..core.search import SearchVideo
from ..i18n import tr


class SearchDownloadStatus(Enum):
    DOWNLOADED = "downloaded"
    MOVED = "moved"
    NOT_DOWNLOADED = "not_downloaded"
    UNIDENTIFIED = "unidentified"


def iwara_history_id(video: SearchVideo) -> str:
    """Use a resolved Iwara identity, never an unresolved Oreno movie ID."""

    video_id = str(video.download_video_id or "").strip()
    if video_id and not video_id.startswith("oreno3d:"):
        return video_id
    raw = video.raw if isinstance(video.raw, dict) else {}
    for url in (video.iwara_url, raw.get("iwara_url")):
        try:
            parsed = urlsplit(str(url or ""))
            host = str(parsed.hostname or "").lower()
        except ValueError:
            continue
        parts = parsed.path.split("/")
        if (
            (host == "iwara.tv" or host.endswith(".iwara.tv"))
            and len(parts) >= 3
            and parts[1] == "video"
            and parts[2]
        ):
            return unquote(parts[2])
    if video.source_kind == "iwara" and not video.video_id.startswith("oreno3d:"):
        return str(video.video_id or "").strip()
    return ""


def download_status_text(status: SearchDownloadStatus) -> str:
    if status is SearchDownloadStatus.DOWNLOADED:
        return tr("Downloaded locally", "本地已下载", "ローカル保存済み")
    if status is SearchDownloadStatus.MOVED:
        return tr("Moved or missing", "已移走", "移動済み・見つからない")
    if status is SearchDownloadStatus.NOT_DOWNLOADED:
        return tr("Not downloaded", "未下载", "未ダウンロード")
    return tr("Not identified yet", "尚未识别", "未識別")


def query_download_statuses(
    history: Any, video_ids: list[str], *, interrupted=None
) -> dict[str, SearchDownloadStatus]:
    """Read one history batch and check only the file paths in those rows."""

    ids = list(dict.fromkeys(str(value or "").strip() for value in video_ids if str(value or "").strip()))
    if not ids:
        return {}
    records = history.get_records(ids)
    statuses: dict[str, SearchDownloadStatus] = {}
    file_exists: dict[str, bool] = {}
    for video_id in ids:
        if interrupted is not None and interrupted():
            return {}
        record = records.get(video_id)
        if record is None:
            statuses[video_id] = SearchDownloadStatus.NOT_DOWNLOADED
            continue
        file_path = str(record.get("file_path") or "").strip()
        if file_path not in file_exists:
            file_exists[file_path] = bool(file_path and os.path.isfile(file_path))
        statuses[video_id] = (
            SearchDownloadStatus.DOWNLOADED if file_exists[file_path] else SearchDownloadStatus.MOVED
        )
    return statuses


class SearchDownloadStatusWorker(QThread):
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
