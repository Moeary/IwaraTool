"""Concurrency limits for search-side background work (Oreno3D resolution, covers)."""

from __future__ import annotations

DEFAULT_SEARCH_RESOLUTION_CONCURRENCY = 4
MAX_SEARCH_RESOLUTION_CONCURRENCY = 8

DEFAULT_COVER_DOWNLOAD_CONCURRENCY = 6
MAX_COVER_DOWNLOAD_CONCURRENCY = 16


def resolution_concurrency(value: int) -> int:
    """``value`` clamped to 1 .. MAX_SEARCH_RESOLUTION_CONCURRENCY."""

    return max(1, min(MAX_SEARCH_RESOLUTION_CONCURRENCY, int(value)))
