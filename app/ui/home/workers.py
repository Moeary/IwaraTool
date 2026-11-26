"""Background workers for the Home page: feeds, detail pages and covers.

The loading itself lives in ``app.core.home.loading`` (each call opens its own
API client, so the sections of the page load concurrently); these workers only
move it off the GUI thread and turn the results into signals.
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, Signal

from ...core.home.feed import MODE_BROWSE
from ...core.download.manager import download_manager
from ...core.home.loading import (  # noqa: F401 - re-exported; tests clear _AUTHOR_IDS here
    _AUTHOR_IDS,
    FeedResult,
    author_user_id,
    call_with_client,
    item_kind,
    items_from_rows,
    load_comments,
    load_detail,
    load_feed_page,
    normalize_items,
)
from ..search.image_worker import SearchImageWorker
from ..common.worker_lifecycle import ManagedThread, stop_qthreads


class FeedWorker(ManagedThread):
    """Load one page of a Home row: ``/videos``, ``/images`` or ``/search``."""

    result_ready = Signal(object)  # FeedResult

    def __init__(
        self,
        token: int,
        kind: str,
        params: dict[str, str],
        page: int,
        limit: int,
        *,
        mode: str = MODE_BROWSE,
        value: str = "",
        rating: str = "",
    ):
        super().__init__()
        self.token = token
        self.kind = item_kind(kind)
        self.params = dict(params)
        self.page = max(0, int(page))
        self.limit = int(limit)
        self.mode = mode
        self.value = value
        self.rating = rating

    def run(self):
        result = load_feed_page(
            download_manager, self.kind, self.params, self.page, self.limit,
            mode=self.mode, value=self.value, rating=self.rating, token=self.token,
        )
        if not self.isInterruptionRequested():
            self.result_ready.emit(result)


class DetailWorker(ManagedThread):
    """Load a post's full record, its related items and the first comments."""

    info_ready = Signal(int, object, str)  # token, dict | None, error
    related_ready = Signal(int, object)  # token, list[SearchVideo]
    comments_ready = Signal(int, object, object, str)  # token, rows, total, error

    def __init__(self, token: int, kind: str, item_id: str):
        super().__init__()
        self.token = token
        self.kind = item_kind(kind)
        self.item_id = item_id

    def run(self):
        load_detail(
            download_manager,
            self.kind,
            self.item_id,
            on_info=lambda info, error: self.info_ready.emit(self.token, info, error),
            on_related=lambda items: self.related_ready.emit(self.token, items),
            on_comments=lambda rows, total, error: self.comments_ready.emit(self.token, rows, total, error),
            should_stop=self.isInterruptionRequested,
        )


class CommentsWorker(ManagedThread):
    """Another page of comments, or the replies to one comment."""

    comments_ready = Signal(int, object, object, str, str, int)  # token, rows, total, error, parent, page

    def __init__(self, token: int, kind: str, item_id: str, *, page: int = 0, parent: str = ""):
        super().__init__()
        self.token = token
        self.kind = item_kind(kind)
        self.item_id = item_id
        self.page = page
        self.parent_id = parent

    def run(self):
        rows, total, error = load_comments(
            download_manager, self.kind, self.item_id, page=self.page, parent=self.parent_id,
        )
        if not self.isInterruptionRequested():
            self.comments_ready.emit(self.token, rows, total, error, self.parent_id, self.page)


class ApiCallWorker(ManagedThread):
    """Run one call against a private API client off the GUI thread.

    ``call(client)`` returns anything; it is delivered through ``done`` (an
    exception becomes ``(None, "message")``-style handling by the caller, so the
    worker reports it as ``error``).
    """

    done = Signal(object, str)  # result, error

    def __init__(self, call, parent: QObject | None = None):
        super().__init__(parent)
        self._call = call

    def run(self):
        result, error = call_with_client(download_manager, self._call)
        if not self.isInterruptionRequested():
            self.done.emit(result, error)


class CoverFetcher(QObject):
    """Download cover/avatar/gallery images once and report each cache path.

    Items are keyed ``kind:key``; a key that is already cached or in flight is
    not requested again, so overlapping grids share their downloads.
    """

    cover_ready = Signal(str, str, str)  # kind, key, local path

    def __init__(self, parent: QObject | None = None, *, concurrency: int = 6):
        super().__init__(parent)
        self._concurrency = concurrency
        self._workers: list[SearchImageWorker] = []
        self._paths: dict[str, str] = {}
        self._pending: set[str] = set()

    def path_for(self, kind: str, key: str) -> str:
        return self._paths.get(f"{kind}:{key}", "")

    def request(self, jobs: list[tuple[str, str, str]]):
        """``jobs`` are ``(kind, key, url)``; cached ones are re-announced."""

        fresh: list[tuple[str, str, str]] = []
        for kind, key, url in jobs:
            if not url:
                continue
            full = f"{kind}:{key}"
            if full in self._paths:
                self.cover_ready.emit(kind, key, self._paths[full])
            elif full not in self._pending:
                self._pending.add(full)
                fresh.append((kind, key, url))
        if not fresh:
            return
        worker = SearchImageWorker(0, fresh, concurrency=self._concurrency)
        self._workers.append(worker)
        worker.image_ready.connect(self._on_ready)
        worker.finished.connect(lambda worker=worker, jobs=fresh: self._on_finished(worker, jobs))
        worker.start()

    def _on_ready(self, _generation: int, kind: str, key: str, path: str):
        full = f"{kind}:{key}"
        self._paths[full] = path
        self._pending.discard(full)
        self.cover_ready.emit(kind, key, path)

    def _on_finished(self, worker: SearchImageWorker, jobs: list[tuple[str, str, str]]):
        if worker in self._workers:
            self._workers.remove(worker)
        for kind, key, _url in jobs:
            self._pending.discard(f"{kind}:{key}")
        worker.deleteLater()

    def interrupt(self):
        for worker in self._workers:
            worker.requestInterruption()

    def shutdown(self, timeout_ms: int = 30_000) -> bool:
        self.interrupt()
        return stop_qthreads(list(self._workers), timeout_ms=timeout_ms)


def stop_workers(workers: list[Any], timeout_ms: int = 30_000) -> bool:
    """Interrupt and wait for ``workers``; already-deleted threads are ignored."""

    for worker in workers:
        try:
            worker.requestInterruption()
        except RuntimeError:
            continue
    return stop_qthreads(list(workers), timeout_ms=timeout_ms)
