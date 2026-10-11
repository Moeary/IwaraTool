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
