"""Concurrency limits for search-side background work (Oreno3D resolution, covers)."""

from __future__ import annotations

DEFAULT_SEARCH_RESOLUTION_CONCURRENCY = 4
MAX_SEARCH_RESOLUTION_CONCURRENCY = 8

DEFAULT_COVER_DOWNLOAD_CONCURRENCY = 6
MAX_COVER_DOWNLOAD_CONCURRENCY = 16


def resolution_concurrency(value: int) -> int:
    """``value`` clamped to 1 .. MAX_SEARCH_RESOLUTION_CONCURRENCY."""

    return max(1, min(MAX_SEARCH_RESOLUTION_CONCURRENCY, int(value)))


def _setting(key: str, default: int, maximum: int) -> int:
    from ...config import app_config

    try:
        value = int(app_config.get_ui_value(key, default) or default)
    except (TypeError, ValueError):
        value = default
    return max(1, min(maximum, value))


def configured_resolution_concurrency() -> int:
    """Settings → Iwara link resolution workers."""
    return _setting("search_iwara_resolution_workers_v1", DEFAULT_SEARCH_RESOLUTION_CONCURRENCY, MAX_SEARCH_RESOLUTION_CONCURRENCY)


def configured_cover_concurrency() -> int:
    """Settings → cover download workers."""
    return _setting("cover_download_workers_v1", DEFAULT_COVER_DOWNLOAD_CONCURRENCY, MAX_COVER_DOWNLOAD_CONCURRENCY)


def resolution_mode() -> str:
    """``eager`` (resolve Oreno3D links as results arrive) or ``on_demand``."""
    from ...config import app_config

    mode = str(app_config.get_ui_value("search_iwara_resolution_mode_v1", "eager") or "eager").strip().lower()
    return mode if mode in {"eager", "on_demand"} else "eager"
