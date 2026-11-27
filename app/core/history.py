"""SQLite-backed download history for metadata persistence."""
from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import closing
from typing import Any

from ..config import app_config
from ..signal_bus import signal_bus
from .download.locations import STORAGE_REMOTE, normalize_storage
from .db.migrate import ensure_current
from .sqlite_utils import connect as _sqlite_connect


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
        "watched_at",
        "storage",
        "remote_path",
    )
    _HEAVY_COLUMNS = frozenset(("tags_json", "raw_json"))
    _LIGHT_COLUMNS = (
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
        "source_url",
        "file_path",
        "thumbnail_path",
        "quality",
        "downloaded_at",
        "watched_at",
        "storage",
        "remote_path",
    )

    def __init__(self, db_path: str | None = None):
        if db_path is None:
            db_path = app_config.history_db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        with closing(_sqlite_connect(self._db_path)) as conn:
            ensure_current(conn, self._db_path)  # db/migrate.py: numbered schema steps

    def _ensure_db_ready(self):
        """Ensure table exists even if history.db was deleted during runtime."""
        self._init_db()

    @staticmethod
    def _is_missing_table_error(exc: sqlite3.OperationalError) -> bool:
        return "no such table" in str(exc).lower()

    def is_downloaded(self, video_id: str) -> bool:
        with self._lock:
            self._ensure_db_ready()
            try:
                with closing(_sqlite_connect(self._db_path)) as conn:
                    row = conn.execute(
                        "SELECT 1 FROM downloaded WHERE video_id=?", (video_id,)
                    ).fetchone()
                    return row is not None
            except sqlite3.OperationalError as exc:
                if not self._is_missing_table_error(exc):
                    raise
                self._ensure_db_ready()
                with closing(_sqlite_connect(self._db_path)) as conn:
                    row = conn.execute(
                        "SELECT 1 FROM downloaded WHERE video_id=?", (video_id,)
                    ).fetchone()
                    return row is not None

    def add_downloaded(self, video_id: str):
        with self._lock:
            self._ensure_db_ready()
            try:
                with closing(_sqlite_connect(self._db_path)) as conn:
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
                with closing(_sqlite_connect(self._db_path)) as conn:
                    conn.execute(
                        "INSERT INTO downloaded (video_id, downloaded_at) VALUES (?, CURRENT_TIMESTAMP) "
                        "ON CONFLICT(video_id) DO UPDATE SET downloaded_at=CURRENT_TIMESTAMP",
                        (video_id,),
                    )
                    conn.commit()

        signal_bus.history_changed.emit()

    def upsert_downloaded(self, meta: dict[str, Any]):
        """Insert or update metadata for a downloaded video."""
        video_id = str(meta.get("video_id", "")).strip()
        if not video_id:
            return
        with self._lock:
            self._ensure_db_ready()
            sql = (
                "INSERT INTO downloaded ("
                "video_id, title, author, published_at, likes, views, slug, rating, duration, comments, tags_json, raw_json, source_url, file_path, thumbnail_path, quality, storage, remote_path, downloaded_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP) "
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
                "storage=excluded.storage, "
                "remote_path=excluded.remote_path, "
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
                normalize_storage(meta.get("storage")),
                str(meta.get("remote_path", "") or "") if normalize_storage(meta.get("storage")) == STORAGE_REMOTE else "",
            )
            try:
                with closing(_sqlite_connect(self._db_path)) as conn:
                    conn.execute(sql, params)
                    conn.commit()
            except sqlite3.OperationalError as exc:
                if not self._is_missing_table_error(exc):
                    raise
                self._ensure_db_ready()
                with closing(_sqlite_connect(self._db_path)) as conn:
                    conn.execute(sql, params)
                    conn.commit()

        signal_bus.history_changed.emit()

    def set_watched(self, video_ids: list[str], watched: bool = True) -> int:
        """Stamp (or clear) when the videos were last played; returns how many rows changed."""

        ids = list(dict.fromkeys(str(v or "").strip() for v in video_ids if str(v or "").strip()))
        if not ids:
            return 0
        value = "CURRENT_TIMESTAMP" if watched else "''"
        changed = 0
        with self._lock:
            self._ensure_db_ready()
            with closing(_sqlite_connect(self._db_path)) as conn:
                for start in range(0, len(ids), 500):
                    chunk = ids[start:start + 500]
                    placeholders = ",".join("?" for _ in chunk)
                    cursor = conn.execute(
                        f"UPDATE downloaded SET watched_at={value} WHERE video_id IN ({placeholders})",
                        chunk,
                    )
                    changed += max(0, int(cursor.rowcount or 0))
                conn.commit()
        if changed:
            signal_bus.history_changed.emit()
        return changed

    def remove(self, video_id: str):
        self.remove_many([video_id])

    def remove_many(self, video_ids: list[str]) -> int:
        """Remove several history records in one SQLite transaction."""
        ids = list(
            dict.fromkeys(
                str(video_id or "").strip()
                for video_id in video_ids
                if str(video_id or "").strip()
            )
        )
        if not ids:
            return 0

        def delete_rows() -> int:
            removed = 0
            with closing(_sqlite_connect(self._db_path)) as conn:
                for start in range(0, len(ids), 500):
                    chunk = ids[start:start + 500]
                    placeholders = ",".join("?" for _ in chunk)
                    cursor = conn.execute(
                        f"DELETE FROM downloaded WHERE video_id IN ({placeholders})",
                        chunk,
                    )
                    removed += max(0, int(cursor.rowcount or 0))
                conn.commit()
            return removed

        with self._lock:
            self._ensure_db_ready()
            try:
                removed = delete_rows()
            except sqlite3.OperationalError as exc:
                if not self._is_missing_table_error(exc):
                    raise
                self._ensure_db_ready()
                removed = delete_rows()
        if removed:
            signal_bus.history_changed.emit()
        return removed

    @classmethod
    def _select_columns(cls, *, include_raw: bool = False) -> tuple[str, ...]:
        return cls._COLUMNS if include_raw else cls._LIGHT_COLUMNS

    def get_record(self, video_id: str, *, include_raw: bool = False) -> dict[str, Any] | None:
        """Return one history row as a dict."""
        video_id = str(video_id or "").strip()
        if not video_id:
            return None
        columns = self._select_columns(include_raw=include_raw)
        with self._lock:
            self._ensure_db_ready()
            try:
                with closing(_sqlite_connect(self._db_path)) as conn:
                    conn.row_factory = sqlite3.Row
                    row = conn.execute(
                        f"SELECT {', '.join(columns)} FROM downloaded WHERE video_id=?",
                        (video_id,),
                    ).fetchone()
                    return dict(row) if row else None
            except sqlite3.OperationalError as exc:
                if not self._is_missing_table_error(exc):
                    raise
                self._ensure_db_ready()
                with closing(_sqlite_connect(self._db_path)) as conn:
                    conn.row_factory = sqlite3.Row
                    row = conn.execute(
                        f"SELECT {', '.join(columns)} FROM downloaded WHERE video_id=?",
                        (video_id,),
                    ).fetchone()
                    return dict(row) if row else None

    def get_records(
        self, video_ids: list[str], *, include_raw: bool = False
    ) -> dict[str, dict[str, Any]]:
        """Return history rows keyed by video_id for a batch of ids."""
        ids = list(dict.fromkeys(str(v or "").strip() for v in video_ids if str(v or "").strip()))
        if not ids:
            return {}
        columns = self._select_columns(include_raw=include_raw)
        result: dict[str, dict[str, Any]] = {}
        with self._lock:
            self._ensure_db_ready()
            try:
                with closing(_sqlite_connect(self._db_path)) as conn:
                    conn.row_factory = sqlite3.Row
                    for start in range(0, len(ids), 500):
                        chunk = ids[start:start + 500]
                        placeholders = ",".join("?" for _ in chunk)
                        rows = conn.execute(
                            f"SELECT {', '.join(columns)} FROM downloaded WHERE video_id IN ({placeholders})",
                            chunk,
                        ).fetchall()
                        for row in rows:
                            data = dict(row)
                            result[str(data.get("video_id", "") or "")] = data
            except sqlite3.OperationalError as exc:
                if not self._is_missing_table_error(exc):
                    raise
                self._ensure_db_ready()
                with closing(_sqlite_connect(self._db_path)) as conn:
                    conn.row_factory = sqlite3.Row
                    for start in range(0, len(ids), 500):
                        chunk = ids[start:start + 500]
                        placeholders = ",".join("?" for _ in chunk)
                        rows = conn.execute(
                            f"SELECT {', '.join(columns)} FROM downloaded WHERE video_id IN ({placeholders})",
                            chunk,
                        ).fetchall()
                        for row in rows:
                            data = dict(row)
                            result[str(data.get("video_id", "") or "")] = data
        return result

    def list_records(self, *, include_raw: bool = False) -> list[dict[str, Any]]:
        """Return all history rows, newest first."""
        columns = self._select_columns(include_raw=include_raw)
        with self._lock:
            self._ensure_db_ready()
            try:
                with closing(_sqlite_connect(self._db_path)) as conn:
                    conn.row_factory = sqlite3.Row
                    rows = conn.execute(
                        f"SELECT {', '.join(columns)} FROM downloaded "
                        "ORDER BY downloaded_at DESC"
                    ).fetchall()
                    return [dict(row) for row in rows]
            except sqlite3.OperationalError as exc:
                if not self._is_missing_table_error(exc):
                    raise
                self._ensure_db_ready()
                with closing(_sqlite_connect(self._db_path)) as conn:
                    conn.row_factory = sqlite3.Row
                    rows = conn.execute(
                        f"SELECT {', '.join(columns)} FROM downloaded "
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
        """Update stored local paths after file operations.

        A local file path makes the row a local one again (a repair or rename
        always works on a file on this machine).
        """
        video_id = str(video_id or "").strip()
        if not video_id:
            return
        sql = (
            "UPDATE downloaded SET file_path=?, thumbnail_path=?, storage='local', remote_path='' WHERE video_id=?"
            if str(file_path or "").strip()
            else "UPDATE downloaded SET file_path=?, thumbnail_path=? WHERE video_id=?"
        )
        with self._lock:
            self._ensure_db_ready()
            try:
                with closing(_sqlite_connect(self._db_path)) as conn:
                    conn.execute(
                        sql, (file_path, thumbnail_path, video_id),
                    )
                    conn.commit()
            except sqlite3.OperationalError as exc:
                if not self._is_missing_table_error(exc):
                    raise
                self._ensure_db_ready()
                with closing(_sqlite_connect(self._db_path)) as conn:
                    conn.execute(
                        sql, (file_path, thumbnail_path, video_id),
                    )
                    conn.commit()

        signal_bus.history_changed.emit()

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
            if normalize_storage(record.get("storage")) == STORAGE_REMOTE:
                # Lives on the aria2 host: this machine cannot tell whether it is gone.
                stats["kept"] += 1
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

        stats["removed"] = self.remove_many(removed_ids)
        return stats

    def all_ids(self) -> list[str]:
        with self._lock:
            self._ensure_db_ready()
            try:
                with closing(_sqlite_connect(self._db_path)) as conn:
                    rows = conn.execute(
                        "SELECT video_id FROM downloaded ORDER BY downloaded_at DESC"
                    ).fetchall()
                    return [r[0] for r in rows]
            except sqlite3.OperationalError as exc:
                if not self._is_missing_table_error(exc):
                    raise
                self._ensure_db_ready()
                with closing(_sqlite_connect(self._db_path)) as conn:
                    rows = conn.execute(
                        "SELECT video_id FROM downloaded ORDER BY downloaded_at DESC"
                    ).fetchall()
                    return [r[0] for r in rows]

    # Fields the library may correct by hand (its "Edit details"); paths change through update_file_paths.
    EDITABLE_COLUMNS = ("title", "author", "quality", "published_at")

    def update_details(self, video_id: str, fields: dict[str, Any]) -> bool:
        """Set some of ``EDITABLE_COLUMNS`` on one row; False when nothing changed or the row is gone."""

        video_id = str(video_id or "").strip()
        values = {key: str(value or "").strip() for key, value in fields.items() if key in self.EDITABLE_COLUMNS}
        if not video_id or not values:
            return False
        assignments = ", ".join(f"{key}=?" for key in values)
        with self._lock:
            self._ensure_db_ready()
            with closing(_sqlite_connect(self._db_path)) as conn:
                cursor = conn.execute(f"UPDATE downloaded SET {assignments} WHERE video_id=?", (*values.values(), video_id))
                conn.commit()
                changed = int(cursor.rowcount or 0) > 0
        if changed:
            signal_bus.history_changed.emit()
        return changed

    def tags_by_id(self) -> dict[str, list[str]]:
        """Every row's saved Iwara tags (``tags_json``), without loading the heavy ``raw_json``."""

        from .nfo import parse_tags

        with self._lock:
            self._ensure_db_ready()
            with closing(_sqlite_connect(self._db_path)) as conn:
                rows = conn.execute("SELECT video_id, tags_json FROM downloaded WHERE tags_json != ''").fetchall()
        return {str(video_id): tags for video_id, tags_json in rows if (tags := parse_tags(str(tags_json or "")))}
