"""Download result covers and avatars in the background."""

from __future__ import annotations

from PySide6.QtCore import Signal

from ...core.search.covers import cache_search_images
from ..common.worker_lifecycle import ManagedThread
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
        cache_search_images(
            download_manager,
            self.jobs,
            lambda kind, item_key, path: self.image_ready.emit(self.generation, kind, item_key, path),
            concurrency=self.concurrency,
            should_stop=self.isInterruptionRequested,
        )
