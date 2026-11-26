"""Fetch one page of search results off the GUI thread."""

from __future__ import annotations

from PySide6.QtCore import Signal

from ...core.search.models import SearchFilters, SearchScope
from ...core.search.service import RATING_SCAN_MAX_PAGES, RATING_SCAN_TARGET, run_search  # noqa: F401
from ..common.worker_lifecycle import ManagedThread
from .page_dependencies import download_manager


class SearchWorker(ManagedThread):
    """Run ``core.search.service.run_search`` for one page off the GUI thread."""

    result_ready = Signal(object)

    def __init__(
        self,
        filters: SearchFilters,
        scope: SearchScope,
        page: int,
        generation: int,
        *,
        replace_results: bool,
        source: str,
    ):
        super().__init__()
        self.filters = filters
        self.scope = scope
        self.page = max(0, int(page))
        self.generation = generation
        self.replace_results = replace_results
        self.source = source

    def run(self):
        self.result_ready.emit(
            run_search(
                self.filters,
                self.scope,
                self.page,
                source=self.source,
                client=download_manager,
                should_stop=self.isInterruptionRequested,
            )
        )
