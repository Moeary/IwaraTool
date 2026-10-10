"""Download result covers and avatars in the background."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from PySide6.QtCore import Signal

from ..worker_lifecycle import ManagedThread
from .page_dependencies import download_manager
from .worker_limits import DEFAULT_COVER_DOWNLOAD_CONCURRENCY, MAX_COVER_DOWNLOAD_CONCURRENCY


class SearchImageWorker(ManagedThread):
    """Download visible card images through the manager's configured session."""

    image_ready = Signal(int, str, str, str)

    def __init__(
        self,
        generation: int,
        jobs: list[tuple[str, str, str]],
        *,
        concurrency: int = DEFAULT_COVER_DOWNLOAD_CONCURRENCY,
    ):
        super().__init__()
        self.generation = generation
        self.jobs = jobs
        self.concurrency = max(1, min(MAX_COVER_DOWNLOAD_CONCURRENCY, int(concurrency)))

    def run(self):
        thread_state = threading.local()
        clients: list[Any] = []
        clients_lock = threading.Lock()

        def fetch(job: tuple[str, str, str]):
            kind, item_key, image_url = job
            if self.isInterruptionRequested():
                return kind, item_key, ""
            client = getattr(thread_state, "api_client", None)
            if client is None:
                create_client = getattr(download_manager, "create_worker_api_client", None)
                if callable(create_client):
                    client = create_client()
                    thread_state.api_client = client
                    with clients_lock:
                        clients.append(client)
            try:
                path = download_manager.cache_search_image(
                    kind,
                    item_key,
                    image_url,
                    api_client=client,
                )
            except TypeError as exc:
                # Preserve compatibility with small manager fakes and older
                # extensions that still expose the three-argument cache method.
                if "api_client" not in str(exc):
                    raise
                path = download_manager.cache_search_image(kind, item_key, image_url)
            return kind, item_key, path

        executor = ThreadPoolExecutor(
            max_workers=min(self.concurrency, len(self.jobs)),
            thread_name_prefix="search-image",
        )
        try:
            futures = [executor.submit(fetch, job) for job in self.jobs]
            for future in as_completed(futures):
                if self.isInterruptionRequested():
                    break
                try:
                    kind, item_key, path = future.result()
                except Exception:
                    continue
                if path:
                    self.image_ready.emit(self.generation, kind, item_key, path)
        finally:
            executor.shutdown(wait=True, cancel_futures=True)
            close_client = getattr(download_manager, "close_worker_api_client", None)
            if callable(close_client):
                for client in clients:
                    close_client(client)
