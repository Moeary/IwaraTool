"""SQLite-backed subscription sources and discovered video items."""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from contextlib import closing
from typing import Any

from ...config import app_config
from ...logging_setup import get_logger
from ..sqlite_utils import connect as _sqlite_connect
from .legacy_migration import LegacyMigrationMixin
from .schema import StoreSchemaMixin
from .source_origin import _default_source_origin, _normalize_source_origin
from .sources_backup import SourcesBackupMixin


logger = get_logger(__name__)


class SubscriptionStore(StoreSchemaMixin, LegacyMigrationMixin, SourcesBackupMixin):
    """Persistent subscription source and update item storage."""

    def __init__(self, db_path: str | None = None, legacy_db_path: str | None = None):
        use_default_db = db_path is None
        if db_path is None:
            db_path = app_config.history_db_path
        if legacy_db_path is None:
            legacy_db_path = (
                os.path.join(app_config.app_data_dir, "subscriptions.db")
                if use_default_db
                else ""
            )
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._db_path = db_path
        self._backup_path = os.path.join(os.path.dirname(db_path), "subscriptions.sources.json")
        self._legacy_db_path = legacy_db_path
        self._lock = threading.Lock()
        self._init_db()
        self._migrate_legacy_db()
        self._restore_sources_backup_if_empty()
        self._write_sources_backup_snapshot()

    @property
    def db_path(self) -> str:
        return self._db_path

    @property
    def backup_path(self) -> str:
        return self._backup_path

    def add_source(
        self,
        source_type: str,
        source_key: str,
        title: str = "",
        remote_id: str = "",
        avatar_url: str = "",
        avatar_path: str = "",
        source_origin: str | None = None,
        source_url: str = "",
    ) -> int:
        source_type = source_type.strip().lower()
        source_key = source_key.strip()
        title = title.strip() or source_key
        source_url = str(source_url or "").strip()
        if not source_type or not source_key:
            return 0
        requested_origin = (
            _normalize_source_origin(source_origin)
            if source_origin is not None
            else ""
        )
        insert_origin = requested_origin or _default_source_origin(source_type)
        with self._lock, closing(_sqlite_connect(self._db_path)) as conn:
            # Iwara usernames are case-insensitive. Reuse the existing author
            # row so an account import can promote a local author source rather
            # than creating a duplicate subscription with a second casing.
            existing = None
            if source_type == "author":
                existing = conn.execute(
                    "SELECT id FROM sources WHERE source_type=? AND lower(source_key)=lower(?) "
                    "ORDER BY id LIMIT 1",
                    (source_type, source_key),
                ).fetchone()
            if existing:
                source_id = int(existing[0])
                self._update_existing_source(
                    conn, source_id, title, remote_id, avatar_url, avatar_path, requested_origin, source_url
                )
                row = (source_id,)
            else:
                conn.execute(
                    "INSERT INTO sources (source_type, source_origin, source_key, title, remote_id, source_url, avatar_url, avatar_path, enabled) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1) "
                    "ON CONFLICT(source_type, source_key) DO UPDATE SET "
                    "title=excluded.title, "
                    "remote_id=CASE WHEN excluded.remote_id != '' THEN excluded.remote_id ELSE remote_id END, "
                    "avatar_url=CASE WHEN excluded.avatar_url != '' THEN excluded.avatar_url ELSE avatar_url END, "
                    "avatar_path=CASE WHEN excluded.avatar_path != '' THEN excluded.avatar_path ELSE avatar_path END, "
                    "source_origin=CASE WHEN excluded.source_origin != '' THEN excluded.source_origin ELSE source_origin END, "
                    "source_url=CASE WHEN excluded.source_url != '' THEN excluded.source_url ELSE source_url END, "
                    "enabled=1, "
                    "updated_at=CURRENT_TIMESTAMP",
                    (source_type, insert_origin, source_key, title, remote_id, source_url, avatar_url, avatar_path),
                )
                row = conn.execute(
                    "SELECT id FROM sources WHERE source_type=? AND source_key=?",
                    (source_type, source_key),
                ).fetchone()
            conn.commit()
            self._export_sources_backup(conn)
            return int(row[0]) if row else 0

    @staticmethod
    def _update_existing_source(conn, source_id: int, title: str, remote_id: str, avatar_url: str, avatar_path: str, requested_origin: str, source_url: str):
        """Refresh an existing row; empty values keep what is stored."""

        conn.execute(
            "UPDATE sources SET "
            "title=?, "
            "remote_id=CASE WHEN ? != '' THEN ? ELSE remote_id END, "
            "avatar_url=CASE WHEN ? != '' THEN ? ELSE avatar_url END, "
            "avatar_path=CASE WHEN ? != '' THEN ? ELSE avatar_path END, "
            "source_origin=CASE WHEN ? != '' THEN ? ELSE source_origin END, "
            "source_url=CASE WHEN ? != '' THEN ? ELSE source_url END, "
            "enabled=1, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (
                title,
                remote_id,
                remote_id,
                avatar_url,
                avatar_url,
                avatar_path,
                avatar_path,
                requested_origin,
                requested_origin,
                source_url,
                source_url,
                source_id,
            ),
        )

    def list_sources(self) -> list[dict[str, Any]]:
        with self._lock, closing(_sqlite_connect(self._db_path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT s.*, "
                "COUNT(i.video_id) AS item_count, "
                "SUM(CASE WHEN i.is_new THEN 1 ELSE 0 END) AS new_count "
                "FROM sources s "
                "LEFT JOIN items i ON i.source_id=s.id "
                "GROUP BY s.id "
                "ORDER BY s.enabled DESC, s.source_type ASC, s.title COLLATE NOCASE ASC"
            ).fetchall()
            return [dict(row) for row in rows]

    def get_source(self, source_id: int) -> dict[str, Any] | None:
        with self._lock, closing(_sqlite_connect(self._db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM sources WHERE id=?",
                (int(source_id),),
            ).fetchone()
            return dict(row) if row else None

    def remove_source(self, source_id: int):
        self.remove_sources([source_id])

    def remove_sources(self, source_ids: list[int]) -> int:
        ids = list(dict.fromkeys(int(source_id) for source_id in source_ids if source_id))
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        with self._lock, closing(_sqlite_connect(self._db_path)) as conn:
            conn.execute(
                f"DELETE FROM items WHERE source_id IN ({placeholders})",
                ids,
            )
            cursor = conn.execute(
                f"DELETE FROM sources WHERE id IN ({placeholders})",
                ids,
            )
            conn.commit()
            self._export_sources_backup(conn)
            return max(0, int(cursor.rowcount or 0))

    def set_source_enabled(self, source_id: int, enabled: bool):
        with self._lock, closing(_sqlite_connect(self._db_path)) as conn:
            conn.execute(
                "UPDATE sources SET enabled=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (1 if enabled else 0, int(source_id)),
            )
            conn.commit()
            self._export_sources_backup(conn)

    def update_source_remote_id(self, source_id: int, remote_id: str):
        with self._lock, closing(_sqlite_connect(self._db_path)) as conn:
            conn.execute(
                "UPDATE sources SET remote_id=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (remote_id, int(source_id)),
            )
            conn.commit()
            self._export_sources_backup(conn)

    def update_source_profile(
        self,
        source_id: int,
        *,
        title: str = "",
        remote_id: str = "",
        avatar_url: str = "",
    ):
        """Persist non-empty profile fields discovered for an author source."""
        with self._lock, closing(_sqlite_connect(self._db_path)) as conn:
            conn.execute(
                "UPDATE sources SET "
                "title=CASE WHEN ? != '' THEN ? ELSE title END, "
                "remote_id=CASE WHEN ? != '' THEN ? ELSE remote_id END, "
                "avatar_url=CASE WHEN ? != '' THEN ? ELSE avatar_url END, "
                "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (
                    str(title or ""),
                    str(title or ""),
                    str(remote_id or ""),
                    str(remote_id or ""),
                    str(avatar_url or ""),
                    str(avatar_url or ""),
                    int(source_id),
                ),
            )
            conn.commit()
            self._export_sources_backup(conn)

    def update_source_avatar(self, source_id: int, avatar_url: str, avatar_path: str):
        with self._lock, closing(_sqlite_connect(self._db_path)) as conn:
            conn.execute(
                "UPDATE sources SET avatar_url=?, avatar_path=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (str(avatar_url or ""), str(avatar_path or ""), int(source_id)),
            )
            conn.commit()
            self._export_sources_backup(conn)

    def touch_source_checked(self, source_id: int):
        with self._lock, closing(_sqlite_connect(self._db_path)) as conn:
            conn.execute(
                "UPDATE sources SET last_checked_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (int(source_id),),
            )
            conn.commit()
            self._export_sources_backup(conn)

    def upsert_items(self, source_id: int, items: list[dict[str, Any]]) -> tuple[int, int]:
        new_count = 0
        with self._lock, closing(_sqlite_connect(self._db_path)) as conn:
            for item in items:
                video_id = str(item.get("video_id", "") or "").strip()
                if not video_id:
                    continue
                row = conn.execute(
                    "SELECT 1 FROM items WHERE source_id=? AND video_id=?",
                    (int(source_id), video_id),
                ).fetchone()
                params = (
                    int(source_id),
                    video_id,
                    str(item.get("title", "") or ""),
                    str(item.get("author", "") or ""),
                    str(item.get("published_at", "") or ""),
                    str(item.get("source_url", "") or ""),
                    str(item.get("thumbnail_url", "") or ""),
                )
                download_state = str(item.get("download_state", "") or "")
                download_reason = str(item.get("download_reason", "") or "")
                state_known = bool(item.get("download_state_known") or download_state or download_reason)
                checked_at = str(item.get("download_checked_at", "") or "")
                if state_known and not checked_at:
                    checked_at = time.strftime("%Y-%m-%d %H:%M:%S")
                if row:
                    if state_known:
                        conn.execute(
                            "UPDATE items SET title=?, author=?, published_at=?, source_url=?, thumbnail_url=COALESCE(NULLIF(?, ''), thumbnail_url), "
                            "download_state=?, download_reason=?, download_checked_at=?, updated_at=CURRENT_TIMESTAMP "
                            "WHERE source_id=? AND video_id=?",
                            (
                                params[2],
                                params[3],
                                params[4],
                                params[5],
                                params[6],
                                download_state,
                                download_reason,
                                checked_at,
                                params[0],
                                params[1],
                            ),
                        )
                        self._write_item_stats(conn, params[0], params[1], item)
                        continue
                    conn.execute(
                        "UPDATE items SET title=?, author=?, published_at=?, source_url=?, thumbnail_url=COALESCE(NULLIF(?, ''), thumbnail_url), updated_at=CURRENT_TIMESTAMP "
                        "WHERE source_id=? AND video_id=?",
                        (params[2], params[3], params[4], params[5], params[6], params[0], params[1]),
                    )
                    self._write_item_stats(conn, params[0], params[1], item)
                    continue
                conn.execute(
                    "INSERT INTO items (source_id, video_id, title, author, published_at, source_url, thumbnail_url, download_state, download_reason, download_checked_at, is_new) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
                    (*params, download_state, download_reason, checked_at),
                )
                self._write_item_stats(conn, params[0], params[1], item)
                new_count += 1
            conn.commit()

        total_count = self.count_items(source_id)
        return new_count, total_count

    @staticmethod
    def _write_item_stats(conn, source_id: int, video_id: str, item: dict[str, Any]):
        """Keep the latest likes / views / tags; a stub without them leaves the old ones."""

        if not item.get("stats_known"):
            return
        conn.execute(
            "UPDATE items SET likes=?, views=?, comments=?, duration=?, rating=?, tags_json=?, stats_known=1 "
            "WHERE source_id=? AND video_id=?",
            (
                int(item.get("likes", 0) or 0),
                int(item.get("views", 0) or 0),
                int(item.get("comments", 0) or 0),
                int(item.get("duration", 0) or 0),
                str(item.get("rating", "") or ""),
                str(item.get("tags_json", "") or ""),
                source_id,
                video_id,
            ),
        )

    def count_items(self, source_id: int) -> int:
        with self._lock, closing(_sqlite_connect(self._db_path)) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM items WHERE source_id=?",
                (int(source_id),),
            ).fetchone()
            return int(row[0] or 0) if row else 0

    def list_items(self, source_id: int | None = None) -> list[dict[str, Any]]:
        params: tuple[Any, ...] = ()
        where = ""
        if source_id:
            where = "WHERE i.source_id=?"
            params = (int(source_id),)
        with self._lock, closing(_sqlite_connect(self._db_path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT i.*, s.source_type, s.source_key, s.title AS source_title, s.enabled AS source_enabled "
                "FROM items i "
                "JOIN sources s ON s.id=i.source_id "
                f"{where} "
                "ORDER BY i.is_new DESC, i.published_at DESC, i.discovered_at DESC",
                params,
            ).fetchall()
            return [dict(row) for row in rows]

    def list_recent_items(self, per_source: int = 8) -> dict[int, list[dict[str, Any]]]:
        """The newest ``per_source`` items of every source, newest first."""

        per_source = max(1, int(per_source))
        with self._lock, closing(_sqlite_connect(self._db_path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM ("
                "SELECT i.*, ROW_NUMBER() OVER ("
                "PARTITION BY i.source_id "
                "ORDER BY i.published_at DESC, i.discovered_at DESC) AS rank_in_source "
                "FROM items i JOIN sources s ON s.id=i.source_id"
                ") WHERE rank_in_source <= ? "
                "ORDER BY source_id, rank_in_source",
                (per_source,),
            ).fetchall()
        recent: dict[int, list[dict[str, Any]]] = {}
        for row in rows:
            data = dict(row)
            recent.setdefault(int(data.get("source_id", 0) or 0), []).append(data)
        return recent

    def get_items_by_video_ids(self, video_ids: list[str]) -> list[dict[str, Any]]:
        ids = list(dict.fromkeys(str(v or "").strip() for v in video_ids if str(v or "").strip()))
        if not ids:
            return []
        result: dict[str, dict[str, Any]] = {}
        with self._lock, closing(_sqlite_connect(self._db_path)) as conn:
            conn.row_factory = sqlite3.Row
            for start in range(0, len(ids), 500):
                chunk = ids[start:start + 500]
                placeholders = ",".join("?" for _ in chunk)
                rows = conn.execute(
                    "SELECT i.*, s.source_type, s.source_key, s.title AS source_title, s.enabled AS source_enabled "
                    "FROM items i "
                    "JOIN sources s ON s.id=i.source_id "
                    f"WHERE i.video_id IN ({placeholders}) "
                    "ORDER BY i.published_at DESC, i.discovered_at DESC",
                    chunk,
                ).fetchall()
                for row in rows:
                    data = dict(row)
                    result.setdefault(str(data.get("video_id", "") or ""), data)
        return [result[video_id] for video_id in ids if video_id in result]

    def mark_items_seen(self, video_ids: list[str]):
        ids = [str(v or "").strip() for v in video_ids if str(v or "").strip()]
        if not ids:
            return
        with self._lock, closing(_sqlite_connect(self._db_path)) as conn:
            conn.executemany(
                "UPDATE items SET is_new=0, updated_at=CURRENT_TIMESTAMP WHERE video_id=?",
                [(video_id,) for video_id in ids],
            )
            conn.commit()

    def update_item_download_state(self, video_id: str, download_state: str, download_reason: str = ""):
        video_id = str(video_id or "").strip()
        if not video_id:
            return
        with self._lock, closing(_sqlite_connect(self._db_path)) as conn:
            conn.execute(
                "UPDATE items SET download_state=?, download_reason=?, download_checked_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP WHERE video_id=?",
                (str(download_state or ""), str(download_reason or ""), video_id),
            )
            conn.commit()

    def update_item_thumbnail_url(self, video_id: str, thumbnail_url: str):
        """Persist a thumbnail URL resolved from an Iwara video detail call."""
        video_id = str(video_id or "").strip()
        thumbnail_url = str(thumbnail_url or "").strip()
        if not video_id or not thumbnail_url:
            return
        with self._lock, closing(_sqlite_connect(self._db_path)) as conn:
            conn.execute(
                "UPDATE items SET thumbnail_url=?, updated_at=CURRENT_TIMESTAMP WHERE video_id=?",
                (thumbnail_url, video_id),
            )
            conn.commit()

    def mark_source_seen(self, source_id: int):
        with self._lock, closing(_sqlite_connect(self._db_path)) as conn:
            conn.execute(
                "UPDATE items SET is_new=0, updated_at=CURRENT_TIMESTAMP WHERE source_id=?",
                (int(source_id),),
            )
            conn.commit()
