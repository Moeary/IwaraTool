"""Resolve Oreno3D cards to Iwara videos in the background (see ``core.search.resolution``)."""

from __future__ import annotations

from PySide6.QtCore import Signal

from ...core.search.models import SearchVideo
from ...core.search.resolution import OrenoLinkResolver, resolve_queue_ids
from ..common.worker_lifecycle import ManagedThread
from .page_dependencies import download_manager
from .worker_limits import DEFAULT_SEARCH_RESOLUTION_CONCURRENCY


class SearchQueueResolveWorker(ManagedThread):
    """Resolve only the selected Oreno3D cards before queueing them."""

    result_ready = Signal(object)

    def __init__(
        self,
        videos: list[SearchVideo],
        *,
        concurrency: int = DEFAULT_SEARCH_RESOLUTION_CONCURRENCY,
    ):
        super().__init__()
        self.videos = videos
        self.concurrency = concurrency

    def run(self):
        self.result_ready.emit(
            resolve_queue_ids(
                download_manager,
                self.videos,
                concurrency=self.concurrency,
                should_stop=self.isInterruptionRequested,
            )
        )


class SearchOrenoLinkWorker(ManagedThread):
    """Resolve Oreno IDs, then hydrate each result from the Iwara API."""

    item_ready = Signal(object)
    result_ready = Signal(object)
    progress = Signal(int, int)

    def __init__(
        self,
        generation: int,
        videos: list[SearchVideo],
        *,
        concurrency: int = DEFAULT_SEARCH_RESOLUTION_CONCURRENCY,
        hydrate_metadata: bool = True,
    ):
        super().__init__()
        self.generation = generation
        self.videos = videos
        self.concurrency = concurrency
        self.hydrate_metadata = bool(hydrate_metadata)

    def run(self):
        resolver = OrenoLinkResolver(
            download_manager,
            self.generation,
            self.videos,
            concurrency=self.concurrency,
            hydrate_metadata=self.hydrate_metadata,
            on_item=self.item_ready.emit,
            on_progress=self.progress.emit,
            should_stop=self.isInterruptionRequested,
        )
        self.result_ready.emit(resolver.run())
