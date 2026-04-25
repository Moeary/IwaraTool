"""SQLite-backed download history for metadata persistence."""
from __future__ import annotations

import os
import sqlite3
import threading
from typing import Any

from ..config import app_config


class DownloadHistory:
    """Thread-safe persistent metadata storage for downloaded videos."""

    _COLUMNS = (
        "video_id",
        "title",
        "author",
        "published_at",
        "likes",
        "views",
        "slug",
        "rating",
        "duration",
        "comments",
        "tags_json",
        "raw_json",
        "source_url",
        "file_path",
        "thumbnail_path",
        "quality",
        "downloaded_at",
    )

    def __init__(self, db_path: str | None = None):
        if db_path is None:
            db_path = app_config.history_db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS downloaded ("
                "video_id TEXT PRIMARY KEY, "
                "title TEXT DEFAULT '', "
                "author TEXT DEFAULT '', "
                "published_at TEXT DEFAULT '', "
                "likes INTEGER DEFAULT 0, "
                "views INTEGER DEFAULT 0, "
                "slug TEXT DEFAULT '', "
                "rating TEXT DEFAULT '', "
                "duration INTEGER DEFAULT 0, "
                "comments INTEGER DEFAULT 0, "
                "tags_json TEXT DEFAULT '', "
                "raw_json TEXT DEFAULT '', "
                "source_url TEXT DEFAULT '', "
                "file_path TEXT DEFAULT '', "
                "thumbnail_path TEXT DEFAULT '', "
                "quality TEXT DEFAULT '', "
                "downloaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
                ")"
            )
            self._ensure_columns(conn)
            conn.commit()

    def _ensure_db_ready(self):
        """Ensure table exists even if history.db was deleted during runtime."""
        self._init_db()

    @staticmethod
    def _is_missing_table_error(exc: sqlite3.OperationalError) -> bool:
        return "no such table" in str(exc).lower()

    @staticmethod
    def _ensure_columns(conn: sqlite3.Connection):
        rows = conn.execute("PRAGMA table_info(downloaded)").fetchall()
        existing = {r[1] for r in rows}
        required: dict[str, str] = {
            "title": "TEXT DEFAULT ''",
            "author": "TEXT DEFAULT ''",
            "published_at": "TEXT DEFAULT ''",
            "likes": "INTEGER DEFAULT 0",
            "views": "INTEGER DEFAULT 0",
            "slug": "TEXT DEFAULT ''",
            "rating": "TEXT DEFAULT ''",
            "duration": "INTEGER DEFAULT 0",
            "comments": "INTEGER DEFAULT 0",
            "tags_json": "TEXT DEFAULT ''",
            "raw_json": "TEXT DEFAULT ''",
            "source_url": "TEXT DEFAULT ''",
            "file_path": "TEXT DEFAULT ''",
            "thumbnail_path": "TEXT DEFAULT ''",
            "quality": "TEXT DEFAULT ''",
        }
        for col, ddl in required.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE downloaded ADD COLUMN {col} {ddl}")

    def is_downloaded(self, video_id: str) -> bool:
        with self._lock:
            self._ensure_db_ready()
            try:
                with sqlite3.connect(self._db_path) as conn:
                    row = conn.execute(
                        "SELECT 1 FROM downloaded WHERE video_id=?", (video_id,)
                    ).fetchone()
                    return row is not None
            except sqlite3.OperationalError as exc:
                if not self._is_missing_table_error(exc):
                    raise
                self._ensure_db_ready()
                with sqlite3.connect(self._db_path) as conn:
                    row = conn.execute(
                        "SELECT 1 FROM downloaded WHERE video_id=?", (video_id,)
                    ).fetchone()
                    return row is not None

    def add_downloaded(self, video_id: str):
        with self._lock:
            self._ensure_db_ready()
            try:
                with sqlite3.connect(self._db_path) as conn:
                    conn.execute(
                        "INSERT INTO downloaded (video_id, downloaded_at) VALUES (?, CURRENT_TIMESTAMP) "
                        "ON CONFLICT(video_id) DO UPDATE SET downloaded_at=CURRENT_TIMESTAMP",
                        (video_id,),
                    )
                    conn.commit()
            except sqlite3.OperationalError as exc:
                if not self._is_missing_table_error(exc):
                    raise
                self._ensure_db_ready()
                with sqlite3.connect(self._db_path) as conn:
                    conn.execute(
                        "INSERT INTO downloaded (video_id, downloaded_at) VALUES (?, CURRENT_TIMESTAMP) "
                        "ON CONFLICT(video_id) DO UPDATE SET downloaded_at=CURRENT_TIMESTAMP",
                        (video_id,),
                    )
                    conn.commit()

    def upsert_downloaded(self, meta: dict[str, Any]):
        """Insert or update metadata for a downloaded video."""
        video_id = str(meta.get("video_id", "")).strip()
        if not video_id:
            return
        with self._lock:
            self._ensure_db_ready()
            sql = (
                "INSERT INTO downloaded ("
                "video_id, title, author, published_at, likes, views, slug, rating, duration, comments, tags_json, raw_json, source_url, file_path, thumbnail_path, quality, downloaded_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP) "
                "ON CONFLICT(video_id) DO UPDATE SET "
                "title=excluded.title, "
                "author=excluded.author, "
                "published_at=excluded.published_at, "
                "likes=excluded.likes, "
                "views=excluded.views, "
                "slug=excluded.slug, "
                "rating=excluded.rating, "
                "duration=excluded.duration, "
                "comments=excluded.comments, "
                "tags_json=excluded.tags_json, "
                "raw_json=excluded.raw_json, "
                "source_url=excluded.source_url, "
                "file_path=excluded.file_path, "
                "thumbnail_path=excluded.thumbnail_path, "
                "quality=excluded.quality, "
                "downloaded_at=CURRENT_TIMESTAMP"
            )
            params = (
                video_id,
                str(meta.get("title", "") or ""),
                str(meta.get("author", "") or ""),
                str(meta.get("published_at", "") or ""),
                int(meta.get("likes", 0) or 0),
                int(meta.get("views", 0) or 0),
                str(meta.get("slug", "") or ""),
                str(meta.get("rating", "") or ""),
                int(meta.get("duration", 0) or 0),
                int(meta.get("comments", 0) or 0),
                str(meta.get("tags_json", "") or ""),
                str(meta.get("raw_json", "") or ""),
                str(meta.get("source_url", "") or ""),
                str(meta.get("file_path", "") or ""),
                str(meta.get("thumbnail_path", "") or ""),
                str(meta.get("quality", "") or ""),
            )
            try:
                with sqlite3.connect(self._db_path) as conn:
                    conn.execute(sql, params)
                    conn.commit()
            except sqlite3.OperationalError as exc:
                if not self._is_missing_table_error(exc):
                    raise
                self._ensure_db_ready()
                with sqlite3.connect(self._db_path) as conn:
                    conn.execute(sql, params)
                    conn.commit()

    def remove(self, video_id: str):
        with self._lock:
            self._ensure_db_ready()
            try:
                with sqlite3.connect(self._db_path) as conn:
                    conn.execute(
                        "DELETE FROM downloaded WHERE video_id=?", (video_id,)
                    )
                    conn.commit()
            except sqlite3.OperationalError as exc:
                if not self._is_missing_table_error(exc):
                    raise
                self._ensure_db_ready()
                with sqlite3.connect(self._db_path) as conn:
                    conn.execute(
                        "DELETE FROM downloaded WHERE video_id=?", (video_id,)
                    )
                    conn.commit()

    def get_record(self, video_id: str) -> dict[str, Any] | None:
        """Return one history row as a dict."""
        video_id = str(video_id or "").strip()
        if not video_id:
            return None
        with self._lock:
            self._ensure_db_ready()
            try:
                with sqlite3.connect(self._db_path) as conn:
                    conn.row_factory = sqlite3.Row
                    row = conn.execute(
                        f"SELECT {', '.join(self._COLUMNS)} FROM downloaded WHERE video_id=?",
                        (video_id,),
                    ).fetchone()
                    return dict(row) if row else None
            except sqlite3.OperationalError as exc:
                if not self._is_missing_table_error(exc):
                    raise
                self._ensure_db_ready()
                with sqlite3.connect(self._db_path) as conn:
                    conn.row_factory = sqlite3.Row
                    row = conn.execute(
                        f"SELECT {', '.join(self._COLUMNS)} FROM downloaded WHERE video_id=?",
                        (video_id,),
                    ).fetchone()
                    return dict(row) if row else None

    def list_records(self) -> list[dict[str, Any]]:
        """Return all history rows, newest first."""
        with self._lock:
            self._ensure_db_ready()
            try:
                with sqlite3.connect(self._db_path) as conn:
                    conn.row_factory = sqlite3.Row
                    rows = conn.execute(
                        f"SELECT {', '.join(self._COLUMNS)} FROM downloaded "
                        "ORDER BY downloaded_at DESC"
                    ).fetchall()
                    return [dict(row) for row in rows]
            except sqlite3.OperationalError as exc:
                if not self._is_missing_table_error(exc):
                    raise
                self._ensure_db_ready()
                with sqlite3.connect(self._db_path) as conn:
                    conn.row_factory = sqlite3.Row
                    rows = conn.execute(
                        f"SELECT {', '.join(self._COLUMNS)} FROM downloaded "
                        "ORDER BY downloaded_at DESC"
                    ).fetchall()
                    return [dict(row) for row in rows]

    def update_file_paths(
        self,
        video_id: str,
        *,
        file_path: str,
        thumbnail_path: str = "",
    ):
        """Update stored local paths after file operations."""
        video_id = str(video_id or "").strip()
        if not video_id:
            return
        with self._lock:
            self._ensure_db_ready()
            try:
                with sqlite3.connect(self._db_path) as conn:
                    conn.execute(
                        "UPDATE downloaded SET file_path=?, thumbnail_path=? WHERE video_id=?",
                        (file_path, thumbnail_path, video_id),
                    )
                    conn.commit()
            except sqlite3.OperationalError as exc:
                if not self._is_missing_table_error(exc):
                    raise
                self._ensure_db_ready()
                with sqlite3.connect(self._db_path) as conn:
                    conn.execute(
                        "UPDATE downloaded SET file_path=?, thumbnail_path=? WHERE video_id=?",
                        (file_path, thumbnail_path, video_id),
                    )
                    conn.commit()

    def sync_with_download_folder(self, download_root: str) -> dict[str, int]:
        """Remove DB records whose files are gone or outside the download root.

        Returns a small stats dict with kept/removed/missing/outside counts.
        """
        records = self.list_records()
        root = os.path.abspath(download_root or "")
        removed_ids: list[str] = []
        stats = {"kept": 0, "removed": 0, "missing": 0, "outside": 0}

        for record in records:
            video_id = str(record.get("video_id", "") or "").strip()
            file_path = str(record.get("file_path", "") or "").strip()
            if not video_id:
                continue

            if not file_path or not os.path.isfile(file_path):
                stats["missing"] += 1
                removed_ids.append(video_id)
                continue

            try:
                file_abs = os.path.abspath(file_path)
                inside_root = os.path.commonpath([root, file_abs]) == root
            except Exception:
                inside_root = False

            if not inside_root:
                stats["outside"] += 1
                removed_ids.append(video_id)
                continue

            stats["kept"] += 1

        for video_id in removed_ids:
            self.remove(video_id)
        stats["removed"] = len(removed_ids)
        return stats

    def all_ids(self) -> list[str]:
        with self._lock:
            self._ensure_db_ready()
            try:
                with sqlite3.connect(self._db_path) as conn:
                    rows = conn.execute(
                        "SELECT video_id FROM downloaded ORDER BY downloaded_at DESC"
                    ).fetchall()
                    return [r[0] for r in rows]
            except sqlite3.OperationalError as exc:
                if not self._is_missing_table_error(exc):
                    raise
                self._ensure_db_ready()
                with sqlite3.connect(self._db_path) as conn:
                    rows = conn.execute(
                        "SELECT video_id FROM downloaded ORDER BY downloaded_at DESC"
                    ).fetchall()
                    return [r[0] for r in rows]
