"""JSON backup of the subscription sources, restored into an empty database."""

from __future__ import annotations

import os
import json
import sqlite3
from contextlib import closing

from ...logging_setup import get_logger
from ..sqlite_utils import connect as _sqlite_connect
from .source_origin import _default_source_origin, _normalize_source_origin


logger = get_logger(__name__)


class SourcesBackupMixin:
    """JSON backup of the subscription sources, restored into an empty database."""

    def _write_sources_backup_snapshot(self):
        with self._lock, closing(_sqlite_connect(self._db_path)) as conn:
            self._export_sources_backup(conn)

    def _restore_sources_backup_if_empty(self):
        if not os.path.exists(self._backup_path):
            return
        with self._lock, closing(_sqlite_connect(self._db_path)) as conn:
            row = conn.execute("SELECT COUNT(*) FROM sources").fetchone()
            if row and int(row[0] or 0) > 0:
                return
            try:
                with open(self._backup_path, "r", encoding="utf-8") as fh:
                    payload = json.load(fh)
            except Exception:
                logger.warning("Unreadable subscription backup %s", self._backup_path, exc_info=True)
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
                    "(source_type, source_origin, source_key, title, remote_id, source_url, avatar_url, avatar_path, enabled, created_at, last_checked_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(NULLIF(?, ''), CURRENT_TIMESTAMP), ?)",
                    (
                        source_type,
                        _normalize_source_origin(source.get("source_origin", ""))
                        or _default_source_origin(source_type),
                        source_key,
                        str(source.get("title", "") or source_key),
                        str(source.get("remote_id", "") or ""),
                        str(source.get("source_url", "") or ""),
                        str(source.get("avatar_url", "") or ""),
                        str(source.get("avatar_path", "") or ""),
                        1 if int(source.get("enabled", 1) or 0) else 0,
                        str(source.get("created_at", "") or ""),
                        str(source.get("last_checked_at", "") or ""),
                    ),
                )
            conn.commit()

    def _export_sources_backup(self, conn: sqlite3.Connection):
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT source_type, source_origin, source_key, title, remote_id, source_url, avatar_url, avatar_path, enabled, created_at, last_checked_at "
            "FROM sources ORDER BY source_type ASC, title COLLATE NOCASE ASC"
        ).fetchall()
        payload = {
            # Adding an optional source URL is backward-compatible with the
            # existing JSON snapshot shape, so keep the established backup
            # version understood by older builds.
            "version": 3,
            "sources": [dict(row) for row in rows],
        }
        tmp_path = f"{self._backup_path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        os.replace(tmp_path, self._backup_path)
