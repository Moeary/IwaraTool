"""Shared, prioritized cover loading for the subscription overview and grid."""

from __future__ import annotations

import os

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QPixmap

from ...config import app_config
from ..media_card import read_pixmap
from .components import SubscriptionThumbnailWorker
from ..worker_lifecycle import stop_qthreads


def load_pixmap(path: str, max_width: int = 360) -> QPixmap | None:
    """A cover from disk, scaled down so hundreds of tiles stay light."""

    if not path or not os.path.isfile(path):
        return None
    pixmap = read_pixmap(path, max_width)  # decoded at the target size, never at full size
    return None if pixmap.isNull() else pixmap


class CoverLoader(QObject):
    """Resolves and caches subscription covers, one small batch at a time.

    Requests made for what the user is looking at *now* (``urgent``: the grid of
    the opened subscription) jump ahead of the background ones (the overview's
    cover strips), so opening a subscription never waits behind hundreds of
    covers for other rows.  A cover that failed to download is forgotten and is
    asked for again the next time something requests it.
    """

    cover_ready = Signal(str, str)  # video id, local path
    queue_changed = Signal()  # something was queued, finished or dropped

    BATCH = 16

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._worker: SubscriptionThumbnailWorker | None = None
        self._queue: list[tuple[str, str, bool]] = []  # video id, url, urgent
        self._requested: set[str] = set()
        self._batch: list[str] = []
        self._delivered: set[str] = set()
        self._stopped = False

    def request(self, requests: list[tuple[str, str]], *, urgent: bool = False):
        fresh: list[tuple[str, str, bool]] = []
        for video_id, url in requests:
            if not video_id:
                continue
            if video_id in self._requested:
                if urgent:
                    # Already waiting behind background work: move it up.
                    for index, (queued_id, queued_url, queued_urgent) in enumerate(self._queue):
                        if queued_id == video_id and not queued_urgent:
                            self._queue[index] = (queued_id, queued_url, True)
                            fresh.append(self._queue.pop(index))
                            break
                continue
            self._requested.add(video_id)
            fresh.append((video_id, url, urgent))
        if urgent:
            self._queue = fresh + self._queue
        else:
            self._queue.extend(fresh)
        self._pump()
        self.queue_changed.emit()

    def _make_worker(self, batch: list[tuple[str, str]]) -> SubscriptionThumbnailWorker:
        try:
            concurrency = max(1, min(8, int(app_config.get_ui_value("cover_download_workers_v1", 6) or 6)))
        except (TypeError, ValueError):
            concurrency = 6
        return SubscriptionThumbnailWorker(batch, concurrency=concurrency)

    def _pump(self):
        if self._stopped or self._worker is not None or not self._queue:
            return
        batch, self._queue = self._queue[: self.BATCH], self._queue[self.BATCH:]
        self._batch = [video_id for video_id, _url, _urgent in batch]
        self._delivered = set()
        worker = self._make_worker([(video_id, url) for video_id, url, _urgent in batch])
        self._worker = worker
        worker.thumbnail_ready.connect(self._on_ready)
        worker.finished.connect(lambda worker=worker: self._on_finished(worker))
        worker.start()

    def _on_ready(self, video_id: str, path: str):
        self._delivered.add(video_id)
        self.cover_ready.emit(video_id, path)

    def _on_finished(self, worker):
        if self._worker is worker:
            self._worker = None
            # Failures are forgotten so a later look at the same covers retries.
            self._requested.difference_update(v for v in self._batch if v not in self._delivered)
            self._batch = []
        worker.deleteLater()
        self._pump()
        self.queue_changed.emit()

    def pending(self) -> int:
        """Covers queued or downloading right now."""

        return len(self._queue) + len(self._batch)

    def drop_background(self):
        """Forget queued non-urgent requests (the overview was left)."""

        kept = [entry for entry in self._queue if entry[2]]
        dropped = [video_id for video_id, _url, urgent in self._queue if not urgent]
        self._queue = kept
        self._requested.difference_update(dropped)
        if dropped:
            self.queue_changed.emit()

    def forget(self, video_ids: list[str]):
        """Allow these covers to be requested again (e.g. after a manual refresh)."""

        self._requested.difference_update(video_ids)

    def shutdown(self, timeout_ms: int = 30_000) -> bool:
        # Keep the reference until the thread's own ``finished``: dropping it
        # here would let a still-running QThread be destroyed.  Safe to call
        # again and again (``timeout_ms=0`` just polls).
        self._stopped = True
        self._queue.clear()
        if self._worker is None:
            return True
        return stop_qthreads([self._worker], timeout_ms=timeout_ms)
