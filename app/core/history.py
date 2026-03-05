"""SQLite-backed download history to prevent re-downloading."""
from __future__ import annotations

import os
import sqlite3
import threading

from ..config import app_config


class DownloadHistory:
    """Thread-safe persistent set of downloaded video IDs."""

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
                "CREATE TABLE IF NOT EXISTS downloaded "
                "(video_id TEXT PRIMARY KEY, downloaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
            )
            conn.commit()

    def is_downloaded(self, video_id: str) -> bool:
        with self._lock:
            with sqlite3.connect(self._db_path) as conn:
                row = conn.execute(
                    "SELECT 1 FROM downloaded WHERE video_id=?", (video_id,)
                ).fetchone()
                return row is not None

    def add_downloaded(self, video_id: str):
        with self._lock:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO downloaded (video_id) VALUES (?)",
                    (video_id,),
                )
                conn.commit()

    def remove(self, video_id: str):
        with self._lock:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute(
                    "DELETE FROM downloaded WHERE video_id=?", (video_id,)
                )
                conn.commit()

    def all_ids(self) -> list[str]:
        with self._lock:
            with sqlite3.connect(self._db_path) as conn:
                rows = conn.execute(
                    "SELECT video_id FROM downloaded ORDER BY downloaded_at DESC"
                ).fetchall()
                return [r[0] for r in rows]
