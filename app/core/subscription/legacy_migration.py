"""Move sources and items from the old subscription database into the current one."""

from __future__ import annotations

import os
import sqlite3
import time

from ..sqlite_utils import connect as _sqlite_connect
from .schema import _table_exists
from .source_origin import _default_source_origin


class LegacyMigrationMixin:
    """Move sources and items from the old subscription database into the current one."""

    def _migrate_legacy_db(self):
        legacy_path = str(self._legacy_db_path or "")
        if (
            not legacy_path
            or not os.path.exists(legacy_path)
            or os.path.abspath(legacy_path) == os.path.abspath(self._db_path)
        ):
            return
        legacy: sqlite3.Connection | None = None
        target: sqlite3.Connection | None = None
        try:
            with self._lock:
                legacy = _sqlite_connect(legacy_path)
                target = _sqlite_connect(self._db_path)
                legacy.row_factory = sqlite3.Row
                target.row_factory = sqlite3.Row
                if not _table_exists(legacy, "sources"):
                    return

                source_id_map = self._copy_legacy_sources(legacy, target)
                if _table_exists(legacy, "items") and source_id_map:
                    self._copy_legacy_items(legacy, target, source_id_map)
                target.commit()
        except sqlite3.Error:
            return
        finally:
            if target is not None:
                target.close()
            if legacy is not None:
                legacy.close()

        self._retire_legacy_db(legacy_path)

    @staticmethod
    def _copy_legacy_sources(legacy, target) -> dict[int, int]:
        """Copy the old sources; returns old source id -> new source id."""

        source_id_map: dict[int, int] = {}
        legacy_sources = legacy.execute("SELECT * FROM sources").fetchall()
        for row in legacy_sources:
            source = dict(row)
            source_type = str(source.get("source_type", "") or "").strip().lower()
            source_key = str(source.get("source_key", "") or "").strip()
            if not source_type or not source_key:
                continue
            target.execute(
                "INSERT INTO sources (source_type, source_origin, source_key, title, remote_id, source_url, enabled, created_at, last_checked_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, COALESCE(NULLIF(?, ''), CURRENT_TIMESTAMP), ?) "
                "ON CONFLICT(source_type, source_key) DO UPDATE SET "
                "source_origin=CASE WHEN excluded.source_origin != '' THEN excluded.source_origin ELSE source_origin END, "
                "title=CASE WHEN excluded.title != '' THEN excluded.title ELSE title END, "
                "remote_id=CASE WHEN excluded.remote_id != '' THEN excluded.remote_id ELSE remote_id END, "
                "enabled=excluded.enabled, "
                "last_checked_at=CASE WHEN excluded.last_checked_at != '' THEN excluded.last_checked_at ELSE last_checked_at END, "
                "updated_at=CURRENT_TIMESTAMP",
                (
                    source_type,
                    _default_source_origin(source_type),
                    source_key,
                    str(source.get("title", "") or source_key),
                    str(source.get("remote_id", "") or ""),
                    str(source.get("source_url", "") or ""),
                    1 if int(source.get("enabled", 1) or 0) else 0,
                    str(source.get("created_at", "") or ""),
                    str(source.get("last_checked_at", "") or ""),
                ),
            )
            target_row = target.execute(
                "SELECT id FROM sources WHERE source_type=? AND source_key=?",
                (source_type, source_key),
            ).fetchone()
            if target_row:
                source_id_map[int(source.get("id", 0) or 0)] = int(target_row["id"])
        return source_id_map

    @staticmethod
    def _copy_legacy_items(legacy, target, source_id_map: dict[int, int]):
        legacy_items = legacy.execute("SELECT * FROM items").fetchall()
        for row in legacy_items:
            item = dict(row)
            new_source_id = source_id_map.get(int(item.get("source_id", 0) or 0))
            video_id = str(item.get("video_id", "") or "").strip()
            if not new_source_id or not video_id:
                continue
            target.execute(
                "INSERT INTO items "
                "(source_id, video_id, title, author, published_at, source_url, thumbnail_url, discovered_at, updated_at, is_new) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(source_id, video_id) DO UPDATE SET "
                "title=CASE WHEN excluded.title != '' THEN excluded.title ELSE title END, "
                "author=CASE WHEN excluded.author != '' THEN excluded.author ELSE author END, "
                "published_at=CASE WHEN excluded.published_at != '' THEN excluded.published_at ELSE published_at END, "
                "source_url=CASE WHEN excluded.source_url != '' THEN excluded.source_url ELSE source_url END, "
                "thumbnail_url=CASE WHEN excluded.thumbnail_url != '' THEN excluded.thumbnail_url ELSE thumbnail_url END, "
                "is_new=MAX(is_new, excluded.is_new), "
                "updated_at=CURRENT_TIMESTAMP",
                (
                    new_source_id,
                    video_id,
                    str(item.get("title", "") or ""),
                    str(item.get("author", "") or ""),
                    str(item.get("published_at", "") or ""),
                    str(item.get("source_url", "") or ""),
                    str(item.get("thumbnail_url", "") or ""),
                    str(item.get("discovered_at", "") or ""),
                    str(item.get("updated_at", "") or ""),
                    1 if int(item.get("is_new", 1) or 0) else 0,
                ),
            )

    @staticmethod
    def _retire_legacy_db(legacy_path: str):
        migrated_path = f"{legacy_path}.migrated"
        if os.path.exists(migrated_path):
            migrated_path = f"{legacy_path}.migrated-{int(time.time())}"
        try:
            os.rename(legacy_path, migrated_path)
        except OSError:
            pass
