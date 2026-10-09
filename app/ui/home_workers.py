"""Background workers for the Home page: feeds, detail pages and covers.

Every worker talks to the API through its own client (``create_worker_api_client``)
so the independent sections of the page load concurrently instead of queueing
behind the shared, lock-guarded session.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from PySide6.QtCore import QObject, Signal

from ..core.home_feed import MODE_AUTHOR, MODE_BROWSE, MODE_KEYWORD, MODE_TAGS
from ..core.manager import download_manager
from ..core.rating import filter_by_rating
from ..core.search import SearchVideo, normalize_image, normalize_video
from .search_workers import SearchImageWorker
from .worker_lifecycle import ManagedThread, stop_qthreads


@dataclass(slots=True)
class FeedResult:
    token: int
    items: list[SearchVideo] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)  # raw API rows, kept for the cache
    total: int | None = None
    has_more: bool = False
    page: int = 0
    error: str = ""


def normalize_items(kind: str, rows: list[dict]) -> list[SearchVideo]:
    """Cards for API rows; old imported image posts with no files left are dropped.

    Their cover is the site's grey "broken file" placeholder and the post has
    nothing to show, which is common in the all-time popular lists.
    """

    if kind == "image":
        rows = [row for row in rows if int(row.get("numImages") or 0) > 0]
    normalize = normalize_image if kind == "image" else normalize_video
    return [item for item in map(normalize, rows) if item is not None]


def items_from_rows(kind: str, rows: list[dict], mode: str = MODE_BROWSE, rating: str = "") -> list[SearchVideo]:
    """Cards for cached or fresh rows; text search results are rated locally."""

    items = normalize_items(kind, rows)
    if mode == MODE_KEYWORD:
        items = filter_by_rating(items, rating)  # /search ignores the rating parameter
    return items


_AUTHOR_IDS: dict[str, str] = {}  # lower-case username -> user id, for this session


def author_user_id(username: str, client) -> tuple[str, str]:
    """``(user id, error)`` for an author's username, remembered per session."""

    key = str(username or "").strip().lstrip("@").casefold()
    if key in _AUTHOR_IDS:
        return _AUTHOR_IDS[key], ""
    user_id, error = client.get_user_id(key)
    if user_id:
        _AUTHOR_IDS[key] = user_id
        return user_id, ""
    return "", error or "author not found"


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
        self.kind = "image" if kind == "image" else "video"
        self.params = dict(params)
        self.page = max(0, int(page))
        self.limit = int(limit)
        self.mode = mode
        self.value = value
        self.rating = rating

    def _fetch(self, client):
        params = dict(self.params)
        if self.mode == MODE_TAGS:
            tags = download_manager.tag_dictionary.resolve_query(params.get("tags", ""))
            if not tags:
                return [], None, False, "no tags"
            params["tags"] = ",".join(tags)
        elif self.mode == MODE_KEYWORD:
            return client.search_page(
                "images" if self.kind == "image" else "videos", params, page=self.page, limit=self.limit,
            )
        elif self.mode == MODE_AUTHOR:
            user_id, error = author_user_id(self.value, client)
            if not user_id:
                return [], None, False, error
            params["user"] = user_id
        return download_manager.get_home_page(
            self.kind, params, page=self.page, limit=self.limit, api_client=client,
        )

    def run(self):
        client = None
        result = FeedResult(token=self.token, page=self.page)
        try:
            client = download_manager.create_worker_api_client()
            rows, total, has_more, error = self._fetch(client)
            result.rows = list(rows) if not error else []
            result.items = items_from_rows(self.kind, rows, self.mode, self.rating)
            result.total, result.has_more, result.error = total, has_more, error
        except Exception as exc:
            result.error = str(exc)
        finally:
            download_manager.close_worker_api_client(client)
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
        self.kind = "image" if kind == "image" else "video"
        self.item_id = item_id

    def run(self):
        client = None
        try:
            client = download_manager.create_worker_api_client()
            if self.kind == "image":
                info, error = download_manager.get_image_info(self.item_id, api_client=client)
            else:
                info, error = client.get_video_info(self.item_id)
            if self.isInterruptionRequested():
                return
            self.info_ready.emit(self.token, info, error)
            if info is None:
                return
            rows, _err = download_manager.get_related_items(self.kind, self.item_id, api_client=client)
            if self.isInterruptionRequested():
                return
            self.related_ready.emit(self.token, normalize_items(self.kind, rows))
            comments, total, comment_error = download_manager.get_item_comments(
                self.kind, self.item_id, page=0, api_client=client,
            )
            if not self.isInterruptionRequested():
                self.comments_ready.emit(self.token, comments, total, comment_error)
        except Exception as exc:
            if not self.isInterruptionRequested():
                self.info_ready.emit(self.token, None, str(exc))
        finally:
            download_manager.close_worker_api_client(client)


class CommentsWorker(ManagedThread):
    """Another page of comments, or the replies to one comment."""

    comments_ready = Signal(int, object, object, str, str, int)  # token, rows, total, error, parent, page

    def __init__(self, token: int, kind: str, item_id: str, *, page: int = 0, parent: str = ""):
        super().__init__()
        self.token = token
        self.kind = "image" if kind == "image" else "video"
        self.item_id = item_id
        self.page = page
        self.parent = parent

    def run(self):
        client = None
        rows: list[dict] = []
        total: int | None = None
        error = ""
        try:
            client = download_manager.create_worker_api_client()
            rows, total, error = download_manager.get_item_comments(
                self.kind, self.item_id, page=self.page, parent=self.parent, api_client=client,
            )
        except Exception as exc:
            error = str(exc)
        finally:
            download_manager.close_worker_api_client(client)
        if not self.isInterruptionRequested():
            self.comments_ready.emit(self.token, rows, total, error, self.parent, self.page)


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
        client = None
        result: Any = None
        error = ""
        try:
            client = download_manager.create_worker_api_client()
            result = self._call(client)
        except Exception as exc:
            error = str(exc)
        finally:
            download_manager.close_worker_api_client(client)
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
