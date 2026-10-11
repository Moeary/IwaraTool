"""Where a subscription source came from (manual, account follow, ...)."""

from __future__ import annotations

from typing import Any


def _normalize_source_origin(value: Any) -> str:
    value = str(value or "").strip().lower()
    aliases = {
        "account_feed": "account",
        "iwara": "account",
        "following": "account",
        "manual": "local",
        "author": "local",
        "list": "playlist",
        "oreno": "oreno3d",
        "oreno3d_author": "oreno3d",
    }
    normalized = aliases.get(value, value)
    return normalized if normalized in {"account", "local", "playlist", "oreno3d"} else ""


def _default_source_origin(source_type: str) -> str:
    source_type = str(source_type or "").strip().lower()
    if source_type == "feed":
        return "account"
    if source_type == "playlist":
        return "playlist"
    return "local"
