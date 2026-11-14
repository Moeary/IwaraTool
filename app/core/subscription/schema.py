"""Create and upgrade the subscription database tables."""

from __future__ import annotations

import sqlite3
from contextlib import closing

from ..sqlite_utils import connect as _sqlite_connect


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return bool(row)


class StoreSchemaMixin:
    """Create and upgrade the subscription database tables."""

    def _init_db(self):
        with closing(_sqlite_connect(self._db_path)) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS sources ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "source_type TEXT NOT NULL, "
                "source_origin TEXT NOT NULL DEFAULT 'local', "
                "source_key TEXT NOT NULL, "
                "title TEXT DEFAULT '', "
                "remote_id TEXT DEFAULT '', "
                "source_url TEXT DEFAULT '', "
                "avatar_url TEXT DEFAULT '', "
                "avatar_path TEXT DEFAULT '', "
                "enabled INTEGER DEFAULT 1, "
                "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
                "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
                "last_checked_at TEXT DEFAULT '', "
                "site TEXT NOT NULL DEFAULT 'tv', "
                "UNIQUE(source_type, source_key, site)"
                ")"
            )
            self._ensure_source_columns(conn)
            self._ensure_source_site(conn)
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

    @staticmethod
    def _ensure_source_columns(conn: sqlite3.Connection):
        rows = conn.execute("PRAGMA table_info(sources)").fetchall()
        existing = {r[1] for r in rows}
        required: dict[str, str] = {
            "source_origin": "TEXT NOT NULL DEFAULT 'local'",
            "avatar_url": "TEXT DEFAULT ''",
            "avatar_path": "TEXT DEFAULT ''",
            "source_url": "TEXT DEFAULT ''",
            # ``created_at`` is present in new databases, but older installs
            # need a migration so the import-time column remains available.
            # SQLite does not allow CURRENT_TIMESTAMP in ALTER TABLE defaults;
            # blank legacy values are filled immediately below.
            "created_at": "TEXT DEFAULT ''",
        }
        origin_was_added = "source_origin" not in existing
        for col, ddl in required.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE sources ADD COLUMN {col} {ddl}")
        # Older databases only knew about the fetch type. Keep that data while
        # assigning a stable origin for the source-status UI.
        conn.execute(
            "UPDATE sources SET source_origin=CASE source_type "
            "WHEN 'feed' THEN 'account' "
            "WHEN 'playlist' THEN 'playlist' "
            "ELSE 'local' END "
            + ("" if origin_was_added else "WHERE source_origin IS NULL OR source_origin=''")
        )
        conn.execute(
            "UPDATE sources SET created_at=CURRENT_TIMESTAMP "
            "WHERE created_at IS NULL OR created_at=''"
        )
        # Feed/list rows cannot be local author subscriptions. This also
        # repairs databases initialized by the first version of this migration
        # before the origin column received type-aware defaults.
        conn.execute(
            "UPDATE sources SET source_origin=CASE source_type "
            "WHEN 'feed' THEN 'account' ELSE 'playlist' END "
            "WHERE source_type IN ('feed', 'playlist')"
        )

    @staticmethod
    def _ensure_source_site(conn: sqlite3.Connection):
        """Give every source its iwara site (``tv`` / ``ai``).

        The same creator or feed can be followed on both sites, so the site is
        part of a source's identity. SQLite cannot change a UNIQUE constraint
        in place: older tables are copied into one with the new constraint,
        keeping their ids (items refer to them). Existing sources were all
        made on iwara.tv.
        """

        columns = [r[1] for r in conn.execute("PRAGMA table_info(sources)").fetchall()]
        if "site" in columns:
            return
        conn.execute("DROP TABLE IF EXISTS sources_with_site")
        conn.execute(
            "CREATE TABLE sources_with_site ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "source_type TEXT NOT NULL, "
            "source_origin TEXT NOT NULL DEFAULT 'local', "
            "source_key TEXT NOT NULL, "
            "title TEXT DEFAULT '', "
            "remote_id TEXT DEFAULT '', "
            "source_url TEXT DEFAULT '', "
            "avatar_url TEXT DEFAULT '', "
            "avatar_path TEXT DEFAULT '', "
            "enabled INTEGER DEFAULT 1, "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "last_checked_at TEXT DEFAULT '', "
            "site TEXT NOT NULL DEFAULT 'tv', "
            "UNIQUE(source_type, source_key, site)"
            ")"
        )
        new_columns = {r[1] for r in conn.execute("PRAGMA table_info(sources_with_site)").fetchall()}
        shared = ", ".join(c for c in columns if c in new_columns)
        conn.execute(f"INSERT INTO sources_with_site ({shared}) SELECT {shared} FROM sources")
        conn.execute("DROP TABLE sources")
        conn.execute("ALTER TABLE sources_with_site RENAME TO sources")

    @staticmethod
    def _ensure_item_columns(conn: sqlite3.Connection):
        rows = conn.execute("PRAGMA table_info(items)").fetchall()
        existing = {r[1] for r in rows}
        required: dict[str, str] = {
            "thumbnail_url": "TEXT DEFAULT ''",
            "download_state": "TEXT DEFAULT ''",
            "download_reason": "TEXT DEFAULT ''",
            "download_checked_at": "TEXT DEFAULT ''",
            # Site stats, so download rules (likes / views / tags) can be applied locally.
            "likes": "INTEGER DEFAULT 0",
            "views": "INTEGER DEFAULT 0",
            "comments": "INTEGER DEFAULT 0",
            "duration": "INTEGER DEFAULT 0",
            "rating": "TEXT DEFAULT ''",
            "tags_json": "TEXT DEFAULT ''",
            "stats_known": "INTEGER DEFAULT 0",  # 0: saved before the stats were kept
        }
        for col, ddl in required.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE items ADD COLUMN {col} {ddl}")
