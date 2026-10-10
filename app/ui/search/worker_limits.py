"""Concurrency limits shared by the search workers and the page."""

from __future__ import annotations


DEFAULT_SEARCH_RESOLUTION_CONCURRENCY = 4


MAX_SEARCH_RESOLUTION_CONCURRENCY = 8


DEFAULT_COVER_DOWNLOAD_CONCURRENCY = 6


MAX_COVER_DOWNLOAD_CONCURRENCY = 16
