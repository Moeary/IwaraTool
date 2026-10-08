"""On-disk cache of what each Home row last showed.

Switching pages must not hit the network: a row is shown from here at once and
only re-checked in the background when its entry has aged past the user's
"refresh after" setting.  The re-check compares a *signature* (the ordered post
ids) so an unchanged feed leaves the grid alone instead of rebuilding it.

Each (section, tab, rating) pair has its own entry, so the account feed, the hot
videos and the hot images cache and expire independently.
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from typing import Any

CACHE_FILENAME = "home_feed_cache.json"
HOME_CACHE_MINUTES_KEY = "home_cache_minutes_v1"  # re-check a row after this long (0 = manual only)
DEFAULT_CACHE_MINUTES = 15
MAX_ENTRIES = 80
ACCOUNT_PREFIX = "acct:"  # entries that belong to the signed-in account


@dataclass(frozen=True)
class CacheEntry:
    rows: list[dict[str, Any]]
    saved_at: float  # epoch seconds
    signature: tuple[str, ...]

    def age(self, now: float | None = None) -> float:
        return max(0.0, (time.time() if now is None else now) - self.saved_at)

    def is_fresh(self, ttl_seconds: float, now: float | None = None) -> bool:
        return ttl_seconds > 0 and self.age(now) < ttl_seconds


def cache_key(section_id: str, tab_id: str, rating: str, *, account: bool = False) -> str:
    prefix = ACCOUNT_PREFIX if account else ""
    return f"{prefix}{section_id}|{tab_id}|{rating or 'all'}"


def rows_signature(rows: list[dict[str, Any]]) -> tuple[str, ...]:
    """Identity of a feed: its post ids in order (counters changing is not news)."""

    return tuple(str(row.get("id") or row.get("video_id") or "") for row in rows if isinstance(row, dict))


class HomeFeedCache:
    """JSON-file cache; every call is safe from any thread."""

    def __init__(self, path: str | None = None):
        self._path = path
        self._lock = threading.RLock()
        self._entries: dict[str, dict[str, Any]] | None = None

    # ── persistence ──────────────────────────────────────────────────────────

    def _resolve_path(self) -> str:
        if self._path:
            return self._path
        from ..config import app_config

        return os.path.join(app_config.app_data_dir, CACHE_FILENAME)

    def _load(self) -> dict[str, dict[str, Any]]:
        if self._entries is None:
            entries: dict[str, dict[str, Any]] = {}
            try:
                with open(self._resolve_path(), "r", encoding="utf-8") as handle:
                    data = json.load(handle)
                if isinstance(data, dict):
                    entries = {
                        str(key): value
                        for key, value in data.items()
                        if isinstance(value, dict) and isinstance(value.get("rows"), list)
                    }
            except (OSError, ValueError):
                entries = {}
            self._entries = entries
        return self._entries

    def _save(self) -> None:
        entries = self._load()
        if len(entries) > MAX_ENTRIES:
            newest = sorted(entries, key=lambda k: float(entries[k].get("saved_at", 0) or 0), reverse=True)
            self._entries = entries = {key: entries[key] for key in newest[:MAX_ENTRIES]}
        path = self._resolve_path()
        tmp = f"{path}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(entries, handle, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp, path)
        except OSError:
            try:
                os.remove(tmp)
            except OSError:
                pass

    # ── api ──────────────────────────────────────────────────────────────────

    def get(self, key: str) -> CacheEntry | None:
        with self._lock:
            raw = self._load().get(key)
            if raw is None:
                return None
            rows = [row for row in raw["rows"] if isinstance(row, dict)]
            try:
                saved_at = float(raw.get("saved_at", 0) or 0)
            except (TypeError, ValueError):
                saved_at = 0.0
            return CacheEntry(rows, saved_at, rows_signature(rows))

    def put(self, key: str, rows: list[dict[str, Any]], *, now: float | None = None) -> bool:
        """Store ``rows``; True when the feed's content differs from what was cached."""

        rows = [row for row in rows if isinstance(row, dict)]
        with self._lock:
            previous = self.get(key)
            changed = previous is None or previous.signature != rows_signature(rows)
            self._load()[key] = {"saved_at": time.time() if now is None else now, "rows": rows}
            self._save()
            return changed

    def touch(self, key: str, *, now: float | None = None) -> None:
        """A re-check found nothing new: restart the entry's freshness clock."""

        with self._lock:
            raw = self._load().get(key)
            if raw is not None:
                raw["saved_at"] = time.time() if now is None else now
                self._save()

    def clear(self, *, account_only: bool = False) -> int:
        with self._lock:
            entries = self._load()
            doomed = [k for k in entries if not account_only or k.startswith(ACCOUNT_PREFIX)]
            for key in doomed:
                del entries[key]
            if doomed:
                self._save()
            return len(doomed)

    def __len__(self) -> int:
        with self._lock:
            return len(self._load())


_shared: HomeFeedCache | None = None


def shared_cache() -> HomeFeedCache:
    global _shared
    if _shared is None:
        _shared = HomeFeedCache()
    return _shared
