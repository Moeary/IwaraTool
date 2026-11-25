"""Concurrency limits shared by the search workers and the page (see ``core.search.limits``)."""

from __future__ import annotations

from ...core.search.limits import (  # noqa: F401
    DEFAULT_COVER_DOWNLOAD_CONCURRENCY,
    DEFAULT_SEARCH_RESOLUTION_CONCURRENCY,
    MAX_COVER_DOWNLOAD_CONCURRENCY,
    MAX_SEARCH_RESOLUTION_CONCURRENCY,
)
