"""SQLite-backed subscription sources and discovered video items."""
from __future__ import annotations

import os
import json
import sqlite3
import threading
import time
from contextlib import closing
from typing import Any

from ..config import app_config


class SubscriptionStore:
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

    def _init_db(self):
        with closing(sqlite3.connect(self._db_path)) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS sources ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "source_type TEXT NOT NULL, "
                "source_key TEXT NOT NULL, "
                "title TEXT DEFAULT '', "
                "remote_id TEXT DEFAULT '', "
                "avatar_url TEXT DEFAULT '', "
                "avatar_path TEXT DEFAULT '', "
                "enabled INTEGER DEFAULT 1, "
                "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
                "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
                "last_checked_at TEXT DEFAULT '', "
                "UNIQUE(source_type, source_key)"
                ")"
            )
            self._ensure_source_columns(conn)
            conn.execute(
                "CREATE TABLE IF NOT EXISTS items ("
                "source_id INTEGER NOT NULL, "
                "video_id TEXT NOT NULL, "
                "title TEXT DEFAULT '', "
                "author TEXT DEFAULT '', "
                "published_at TEXT DEFAULT '', "
                "source_url TEXT DEFAULT '', "
                "thumbnail_url TEXT DEFAULT '', "
                "download_state TEXT DEFAULT '', "
                "download_reason TEXT DEFAULT '', "
                "download_checked_at TEXT DEFAULT '', "
                "discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
                "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
                "is_new INTEGER DEFAULT 1, "
                "PRIMARY KEY(source_id, video_id)"
                ")"
            )
            self._ensure_item_columns(conn)
            conn.commit()

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
                legacy = sqlite3.connect(legacy_path)
                target = sqlite3.connect(self._db_path)
                legacy.row_factory = sqlite3.Row
                target.row_factory = sqlite3.Row
                if not _table_exists(legacy, "sources"):
                    return

                source_id_map: dict[int, int] = {}
                legacy_sources = legacy.execute("SELECT * FROM sources").fetchall()
                for row in legacy_sources:
                    source = dict(row)
                    source_type = str(source.get("source_type", "") or "").strip().lower()
                    source_key = str(source.get("source_key", "") or "").strip()
                    if not source_type or not source_key:
                        continue
                    target.execute(
                        "INSERT INTO sources (source_type, source_key, title, remote_id, enabled, last_checked_at) "
                        "VALUES (?, ?, ?, ?, ?, ?) "
                        "ON CONFLICT(source_type, source_key) DO UPDATE SET "
                        "title=CASE WHEN excluded.title != '' THEN excluded.title ELSE title END, "
                        "remote_id=CASE WHEN excluded.remote_id != '' THEN excluded.remote_id ELSE remote_id END, "
                        "enabled=excluded.enabled, "
                        "last_checked_at=CASE WHEN excluded.last_checked_at != '' THEN excluded.last_checked_at ELSE last_checked_at END, "
                        "updated_at=CURRENT_TIMESTAMP",
                        (
                            source_type,
                            source_key,
                            str(source.get("title", "") or source_key),
                            str(source.get("remote_id", "") or ""),
                            1 if int(source.get("enabled", 1) or 0) else 0,
                            str(source.get("last_checked_at", "") or ""),
                        ),
                    )
                    target_row = target.execute(
                        "SELECT id FROM sources WHERE source_type=? AND source_key=?",
                        (source_type, source_key),
                    ).fetchone()
                    if target_row:
                        source_id_map[int(source.get("id", 0) or 0)] = int(target_row["id"])

                if _table_exists(legacy, "items") and source_id_map:
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
                target.commit()
        except sqlite3.Error:
            return
        finally:
            if target is not None:
                target.close()
            if legacy is not None:
                legacy.close()

        migrated_path = f"{legacy_path}.migrated"
        if os.path.exists(migrated_path):
            migrated_path = f"{legacy_path}.migrated-{int(time.time())}"
        try:
            os.rename(legacy_path, migrated_path)
        except OSError:
            pass

    def add_source(
        self,
        source_type: str,
        source_key: str,
        title: str = "",
        remote_id: str = "",
        avatar_url: str = "",
        avatar_path: str = "",
    ) -> int:
        source_type = source_type.strip().lower()
        source_key = source_key.strip()
        title = title.strip() or source_key
        if not source_type or not source_key:
            return 0
        with self._lock, closing(sqlite3.connect(self._db_path)) as conn:
            conn.execute(
                "INSERT INTO sources (source_type, source_key, title, remote_id, avatar_url, avatar_path, enabled) "
                "VALUES (?, ?, ?, ?, ?, ?, 1) "
                "ON CONFLICT(source_type, source_key) DO UPDATE SET "
                "title=excluded.title, "
                "remote_id=CASE WHEN excluded.remote_id != '' THEN excluded.remote_id ELSE remote_id END, "
                "avatar_url=CASE WHEN excluded.avatar_url != '' THEN excluded.avatar_url ELSE avatar_url END, "
                "avatar_path=CASE WHEN excluded.avatar_path != '' THEN excluded.avatar_path ELSE avatar_path END, "
                "enabled=1, "
                "updated_at=CURRENT_TIMESTAMP",
                (source_type, source_key, title, remote_id, avatar_url, avatar_path),
            )
            row = conn.execute(
                "SELECT id FROM sources WHERE source_type=? AND source_key=?",
                (source_type, source_key),
            ).fetchone()
            conn.commit()
            self._export_sources_backup(conn)
            return int(row[0]) if row else 0

    def list_sources(self) -> list[dict[str, Any]]:
        with self._lock, closing(sqlite3.connect(self._db_path)) as conn:
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
        with self._lock, closing(sqlite3.connect(self._db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM sources WHERE id=?",
                (int(source_id),),
            ).fetchone()
            return dict(row) if row else None

    def remove_source(self, source_id: int):
        with self._lock, closing(sqlite3.connect(self._db_path)) as conn:
            conn.execute("DELETE FROM items WHERE source_id=?", (int(source_id),))
            conn.execute("DELETE FROM sources WHERE id=?", (int(source_id),))
            conn.commit()
            self._export_sources_backup(conn)

    def set_source_enabled(self, source_id: int, enabled: bool):
        with self._lock, closing(sqlite3.connect(self._db_path)) as conn:
            conn.execute(
                "UPDATE sources SET enabled=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (1 if enabled else 0, int(source_id)),
            )
            conn.commit()
            self._export_sources_backup(conn)

    def update_source_remote_id(self, source_id: int, remote_id: str):
        with self._lock, closing(sqlite3.connect(self._db_path)) as conn:
            conn.execute(
                "UPDATE sources SET remote_id=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (remote_id, int(source_id)),
            )
            conn.commit()
            self._export_sources_backup(conn)

    def update_source_avatar(self, source_id: int, avatar_url: str, avatar_path: str):
        with self._lock, closing(sqlite3.connect(self._db_path)) as conn:
            conn.execute(
                "UPDATE sources SET avatar_url=?, avatar_path=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (str(avatar_url or ""), str(avatar_path or ""), int(source_id)),
            )
            conn.commit()
            self._export_sources_backup(conn)

    def touch_source_checked(self, source_id: int):
        with self._lock, closing(sqlite3.connect(self._db_path)) as conn:
            conn.execute(
                "UPDATE sources SET last_checked_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (int(source_id),),
            )
            conn.commit()
            self._export_sources_backup(conn)

    def upsert_items(self, source_id: int, items: list[dict[str, Any]]) -> tuple[int, int]:
        new_count = 0
        with self._lock, closing(sqlite3.connect(self._db_path)) as conn:
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
                            "UPDATE items SET title=?, author=?, published_at=?, source_url=?, thumbnail_url=?, "
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
                        continue
                    conn.execute(
                        "UPDATE items SET title=?, author=?, published_at=?, source_url=?, thumbnail_url=?, updated_at=CURRENT_TIMESTAMP "
                        "WHERE source_id=? AND video_id=?",
                        (params[2], params[3], params[4], params[5], params[6], params[0], params[1]),
                    )
                    continue
                conn.execute(
                    "INSERT INTO items (source_id, video_id, title, author, published_at, source_url, thumbnail_url, download_state, download_reason, download_checked_at, is_new) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
                    (*params, download_state, download_reason, checked_at),
                )
                new_count += 1
            conn.commit()

        total_count = self.count_items(source_id)
        return new_count, total_count

    def count_items(self, source_id: int) -> int:
        with self._lock, closing(sqlite3.connect(self._db_path)) as conn:
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
        with self._lock, closing(sqlite3.connect(self._db_path)) as conn:
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

    def get_items_by_video_ids(self, video_ids: list[str]) -> list[dict[str, Any]]:
        ids = list(dict.fromkeys(str(v or "").strip() for v in video_ids if str(v or "").strip()))
        if not ids:
            return []
        result: dict[str, dict[str, Any]] = {}
        with self._lock, closing(sqlite3.connect(self._db_path)) as conn:
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
        with self._lock, closing(sqlite3.connect(self._db_path)) as conn:
            conn.executemany(
                "UPDATE items SET is_new=0, updated_at=CURRENT_TIMESTAMP WHERE video_id=?",
                [(video_id,) for video_id in ids],
            )
            conn.commit()

    def update_item_download_state(self, video_id: str, download_state: str, download_reason: str = ""):
        video_id = str(video_id or "").strip()
        if not video_id:
            return
        with self._lock, closing(sqlite3.connect(self._db_path)) as conn:
            conn.execute(
                "UPDATE items SET download_state=?, download_reason=?, download_checked_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP WHERE video_id=?",
                (str(download_state or ""), str(download_reason or ""), video_id),
            )
            conn.commit()

    def mark_source_seen(self, source_id: int):
        with self._lock, closing(sqlite3.connect(self._db_path)) as conn:
            conn.execute(
                "UPDATE items SET is_new=0, updated_at=CURRENT_TIMESTAMP WHERE source_id=?",
                (int(source_id),),
            )
            conn.commit()

    def _write_sources_backup_snapshot(self):
        with self._lock, closing(sqlite3.connect(self._db_path)) as conn:
            self._export_sources_backup(conn)

    def _restore_sources_backup_if_empty(self):
        if not os.path.exists(self._backup_path):
            return
        with self._lock, closing(sqlite3.connect(self._db_path)) as conn:
            row = conn.execute("SELECT COUNT(*) FROM sources").fetchone()
            if row and int(row[0] or 0) > 0:
                return
            try:
                with open(self._backup_path, "r", encoding="utf-8") as fh:
                    payload = json.load(fh)
            except Exception:
                return
            sources = payload.get("sources") if isinstance(payload, dict) else None
            if not isinstance(sources, list):
                return
            for source in sources:
                if not isinstance(source, dict):
                    continue
                source_type = str(source.get("source_type", "") or "").strip().lower()
                source_key = str(source.get("source_key", "") or "").strip()
                if not source_type or not source_key:
                    continue
                conn.execute(
                    "INSERT OR IGNORE INTO sources "
                    "(source_type, source_key, title, remote_id, avatar_url, avatar_path, enabled, last_checked_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        source_type,
                        source_key,
                        str(source.get("title", "") or source_key),
                        str(source.get("remote_id", "") or ""),
                        str(source.get("avatar_url", "") or ""),
                        str(source.get("avatar_path", "") or ""),
                        1 if int(source.get("enabled", 1) or 0) else 0,
                        str(source.get("last_checked_at", "") or ""),
                    ),
                )
            conn.commit()

    def _export_sources_backup(self, conn: sqlite3.Connection):
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT source_type, source_key, title, remote_id, avatar_url, avatar_path, enabled, last_checked_at "
            "FROM sources ORDER BY source_type ASC, title COLLATE NOCASE ASC"
        ).fetchall()
        payload = {
            "version": 1,
            "sources": [dict(row) for row in rows],
        }
        tmp_path = f"{self._backup_path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        os.replace(tmp_path, self._backup_path)

    @staticmethod
    def _ensure_source_columns(conn: sqlite3.Connection):
        rows = conn.execute("PRAGMA table_info(sources)").fetchall()
        existing = {r[1] for r in rows}
        required: dict[str, str] = {
            "avatar_url": "TEXT DEFAULT ''",
            "avatar_path": "TEXT DEFAULT ''",
        }
        for col, ddl in required.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE sources ADD COLUMN {col} {ddl}")

    @staticmethod
    def _ensure_item_columns(conn: sqlite3.Connection):
        rows = conn.execute("PRAGMA table_info(items)").fetchall()
        existing = {r[1] for r in rows}
        required: dict[str, str] = {
            "thumbnail_url": "TEXT DEFAULT ''",
            "download_state": "TEXT DEFAULT ''",
            "download_reason": "TEXT DEFAULT ''",
            "download_checked_at": "TEXT DEFAULT ''",
        }
        for col, ddl in required.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE items ADD COLUMN {col} {ddl}")


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return bool(row)
