"""Batch subscription work for the interfaces: refreshing sources, avatars and covers.

These run off the UI thread (a ``ManagedThread`` in PySide6,
``asyncio.to_thread`` in Flet) and report progress through callbacks.
``manager`` is the download manager (passed in so tests can replace it);
``should_stop`` follows the current ``CancelToken`` (or QThread interruption)
unless given.
"""
from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from ..net_policy import cancellation_requested
from ..search.limits import DEFAULT_COVER_DOWNLOAD_CONCURRENCY, MAX_COVER_DOWNLOAD_CONCURRENCY


def source_id_list(source_id: int | Iterable[int] | None) -> list[int]:
    """Distinct source IDs, in order, from one ID, several, or none (meaning all)."""

    if isinstance(source_id, (list, tuple, set)):
        return list(dict.fromkeys(int(value) for value in source_id if value))
    return [int(source_id)] if source_id else []


def refresh_sources(
    manager: Any,
    source_ids: list[int],
    *,
    ignore_disabled: bool = False,
    on_progress: Callable[[dict[str, Any]], None] = lambda _event: None,
) -> dict[str, Any]:
    """Refresh the given sources one by one (all sources when empty); the combined summary.

    ``on_progress`` gets ``{"stage": "started"|"finished", "index", "total",
    "source_id", "title"[, "summary"]}`` around each source; with no IDs it
    receives the manager's own progress events instead.
    """

    if not source_ids:
        return manager.refresh_all_subscriptions(on_progress)
    summaries: list[dict[str, Any]] = []
    total = len(source_ids)
    for index, source_id in enumerate(source_ids, start=1):
        source = manager.subscriptions.get_source(source_id) or {}
        title = str(source.get("title", "") or source.get("source_key", "") or "")
        on_progress({"stage": "started", "index": index, "total": total, "source_id": source_id, "title": title})
        try:
            result = manager.refresh_subscription_source(source_id, ignore_enabled=ignore_disabled)
        except Exception as exc:
            result = {"source_id": source_id, "title": title, "error": str(exc)}
        result = dict(result or {})
        result.setdefault("source_id", source_id)
        result.setdefault("title", title)
        summaries.append(result)
        on_progress({
            "stage": "finished",
            "index": index,
            "total": total,
            "source_id": source_id,
            "title": str(result.get("title", "") or title),
            "summary": result,
        })
    return manager._subscription_refresh_summary(summaries)


def refresh_avatars(
    manager: Any,
    source_ids: Iterable[int],
    on_avatar: Callable[[int, str, str], None],
    *,
    should_stop: Callable[[], bool] = cancellation_requested,
) -> None:
    """Fetch each source's avatar; ``on_avatar(source_id, url, local path)`` for each found."""

    for source_id in source_ids:
        if should_stop():
            return
        result = manager.refresh_subscription_source_avatar(source_id)
        avatar_path = str(result.get("avatar_path", "") or "")
        if avatar_path:
            on_avatar(
                int(result.get("source_id", source_id) or source_id),
                str(result.get("avatar_url", "") or ""),
                avatar_path,
            )


def cover_concurrency(value: int) -> int:
    return max(1, min(MAX_COVER_DOWNLOAD_CONCURRENCY, int(value)))


def cache_thumbnails(
    manager: Any,
    requests: list[tuple[str, str]],
    on_ready: Callable[[str, str], None],
    *,
    force: bool = False,
    concurrency: int = DEFAULT_COVER_DOWNLOAD_CONCURRENCY,
    should_stop: Callable[[], bool] = cancellation_requested,
) -> tuple[int, int]:
    """Download ``(video_id, thumbnail_url)`` covers in parallel; ``(succeeded, failed)``.

    ``on_ready(video_id, local path)`` is called from this thread as each one
    lands.  Each pool thread uses its own API client, closed at the end.
    """

    if not requests:
        return 0, 0
    thread_state = threading.local()
    clients: list[Any] = []
    clients_lock = threading.Lock()
    create_client = getattr(manager, "create_worker_api_client", None)

    def fetch(request: tuple[str, str]) -> tuple[str, str]:
        video_id, thumbnail_url = request
        if should_stop():
            return video_id, ""
        client = getattr(thread_state, "api_client", None)
        if client is None and callable(create_client):
            client = create_client()
            thread_state.api_client = client
            with clients_lock:
                clients.append(client)
        try:
            path = manager.cache_subscription_thumbnail(video_id, thumbnail_url, force=force, api_client=client)
            return video_id, path or ""
        except Exception:
            return video_id, ""

    succeeded = failed = 0
    executor = ThreadPoolExecutor(
        max_workers=min(cover_concurrency(concurrency), len(requests)),
        thread_name_prefix="subscription-cover",
    )
    try:
        futures = [executor.submit(fetch, request) for request in requests]
        for future in as_completed(futures):
            if should_stop():
                break
            video_id, path = future.result()
            if path:
                succeeded += 1
                on_ready(video_id, path)
            else:
                failed += 1
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
        close_client = getattr(manager, "close_worker_api_client", None)
        if callable(close_client):
            for client in clients:
                close_client(client)
    return succeeded, failed
