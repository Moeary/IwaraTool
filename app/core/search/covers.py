"""Download search result covers and avatars through the manager's image cache.

Runs off the UI thread (a ``ManagedThread`` in PySide6, ``asyncio.to_thread``
in Flet).  Each pool thread uses its own API client, closed at the end.
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from ..net_policy import cancellation_requested
from .limits import DEFAULT_COVER_DOWNLOAD_CONCURRENCY, MAX_COVER_DOWNLOAD_CONCURRENCY

ImageJob = tuple[str, str, str]  # (kind: "video" / "author" / "playlist", item key, image URL)


def cache_search_images(
    manager: Any,
    jobs: list[ImageJob],
    on_ready: Callable[[str, str, str], None],
    *,
    concurrency: int = DEFAULT_COVER_DOWNLOAD_CONCURRENCY,
    should_stop: Callable[[], bool] = cancellation_requested,
) -> None:
    """Cache each ``(kind, key, url)``; ``on_ready(kind, key, local path)`` runs on this thread as each lands."""

    if not jobs:
        return
    concurrency = max(1, min(MAX_COVER_DOWNLOAD_CONCURRENCY, int(concurrency)))
    thread_state = threading.local()
    clients: list[Any] = []
    clients_lock = threading.Lock()

    def fetch(job: ImageJob) -> ImageJob:
        kind, item_key, image_url = job
        if should_stop():
            return kind, item_key, ""
        client = getattr(thread_state, "api_client", None)
        if client is None:
            create_client = getattr(manager, "create_worker_api_client", None)
            if callable(create_client):
                client = create_client()
                thread_state.api_client = client
                with clients_lock:
                    clients.append(client)
        try:
            path = manager.cache_search_image(kind, item_key, image_url, api_client=client)
        except TypeError as exc:
            # Small manager fakes and older extensions expose the three-argument method.
            if "api_client" not in str(exc):
                raise
            path = manager.cache_search_image(kind, item_key, image_url)
        return kind, item_key, path

    executor = ThreadPoolExecutor(max_workers=min(concurrency, len(jobs)), thread_name_prefix="search-image")
    try:
        futures = [executor.submit(fetch, job) for job in jobs]
        for future in as_completed(futures):
            if should_stop():
                break
            try:
                kind, item_key, path = future.result()
            except Exception:
                continue
            if path:
                on_ready(kind, item_key, path)
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
        close_client = getattr(manager, "close_worker_api_client", None)
        if callable(close_client):
            for client in clients:
                close_client(client)
