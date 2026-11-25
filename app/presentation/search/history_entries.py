"""Normalize, merge and decode stored search-history entries."""

from __future__ import annotations

import json


DEFAULT_SEARCH_HISTORY_LIMIT = 20


MAX_SEARCH_HISTORY_LIMIT = 100


def _normalize_search_history_entry(value: object) -> dict[str, str] | None:
    """Keep one persisted search-history item small, valid, and portable."""

    if not isinstance(value, dict):
        return None
    entry = {
        "keyword": str(value.get("keyword") or "").strip(),
        "source": str(value.get("source") or "oreno3d").strip().lower(),
        "scope": str(value.get("scope") or "videos").strip().lower(),
        "sort": str(value.get("sort") or "date").strip().lower(),
    }
    if entry["source"] not in {"oreno3d", "iwara"}:
        entry["source"] = "oreno3d"
    if entry["scope"] not in {"videos", "images", "authors", "tags", "playlists"}:
        entry["scope"] = "videos"
    if not entry["keyword"] and entry["scope"] != "videos":
        return None
    return entry


def _upsert_search_history(
    history: object,
    entry: object,
    limit: int = DEFAULT_SEARCH_HISTORY_LIMIT,
) -> list[dict[str, str]]:
    """Return an MRU history list with duplicate queries collapsed."""

    # An entry not worth keeping (no keyword outside video browsing) leaves
    # the existing history as it is.
    normalized_entry = _normalize_search_history_entry(entry)
    try:
        safe_limit = max(1, min(MAX_SEARCH_HISTORY_LIMIT, int(limit)))
    except (TypeError, ValueError):
        safe_limit = DEFAULT_SEARCH_HISTORY_LIMIT

    values = history if isinstance(history, list) else []
    normalized: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for value in [normalized_entry, *values] if normalized_entry is not None else values:
        item = _normalize_search_history_entry(value)
        if item is None:
            continue
        key = tuple(item[field].casefold() for field in ("keyword", "source", "scope", "sort"))
        if key in seen:
            continue
        seen.add(key)
        normalized.append(item)
        if len(normalized) >= safe_limit:
            break
    return normalized


def _decode_search_history(value: object) -> list[dict[str, str]]:
    """Decode persisted JSON while tolerating old or manually edited values."""

    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        return []
    decoded: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for candidate in value:
        item = _normalize_search_history_entry(candidate)
        if item is None:
            continue
        key = tuple(item[field].casefold() for field in ("keyword", "source", "scope", "sort"))
        if key in seen:
            continue
        seen.add(key)
        decoded.append(item)
    return decoded


SEARCH_HISTORY_KEY = "search_history_v1"


def read_search_history() -> list[dict[str, str]]:
    from ...config import app_config

    history = _decode_search_history(app_config.get_ui_value(SEARCH_HISTORY_KEY, "[]"))
    return history[: app_config.search_history_limit]


def record_search(keyword: str, source: str, scope: str, sort: str) -> list[dict[str, str]]:
    """Put this query on top of the saved recent searches; returns the new list."""

    import json

    from ...config import app_config

    history = _upsert_search_history(
        _decode_search_history(app_config.get_ui_value(SEARCH_HISTORY_KEY, "[]")),
        {"keyword": str(keyword or "").strip(), "source": source, "scope": scope, "sort": sort},
        app_config.search_history_limit,
    )
    app_config.set_ui_value(SEARCH_HISTORY_KEY, json.dumps(history, ensure_ascii=False, separators=(",", ":")))
    return history


def search_history_label(entry: dict[str, str]) -> str:
    from ...i18n import tr
    from .options import scope_items, source_label

    keyword = entry.get("keyword", "") or tr("Latest videos", "最新视频", "最新動画")
    scopes = dict((key, label) for label, key in scope_items())
    scope = scopes.get(entry.get("scope", "videos"), entry.get("scope", "videos"))
    source_key = entry.get("source", "oreno3d")
    source = source_label(source_key, short=True) if source_key in ("iwara", "oreno3d") else source_key
    return f"{keyword} · {scope} · {source}"


def clear_search_history() -> None:
    from ...config import app_config

    app_config.set_ui_value(SEARCH_HISTORY_KEY, "[]")
