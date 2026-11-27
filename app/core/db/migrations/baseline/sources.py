"""``sources``: subscriptions (authors, the account feed, playlists) on either site."""
from __future__ import annotations

import sqlite3

from . import add_missing, columns

TABLE_BODY = (
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
)

LATER_COLUMNS: dict[str, str] = {
    "source_origin": "TEXT NOT NULL DEFAULT 'local'",
    "avatar_url": "TEXT DEFAULT ''",
    "avatar_path": "TEXT DEFAULT ''",
    "source_url": "TEXT DEFAULT ''",
    # SQLite does not allow CURRENT_TIMESTAMP in ALTER TABLE defaults; blanks are filled below.
    "created_at": "TEXT DEFAULT ''",
}


def create_or_catch_up(conn: sqlite3.Connection) -> None:
    conn.execute(f"CREATE TABLE IF NOT EXISTS sources ({TABLE_BODY})")
    _catch_up_columns(conn)
    _add_site(conn)


def _catch_up_columns(conn: sqlite3.Connection) -> None:
    added = add_missing(conn, "sources", LATER_COLUMNS)
    # Older files knew only the fetch type; derive the origin shown in the source list from it.
    conn.execute(
        "UPDATE sources SET source_origin=CASE source_type "
        "WHEN 'feed' THEN 'account' WHEN 'playlist' THEN 'playlist' ELSE 'local' END "
        + ("" if "source_origin" in added else "WHERE source_origin IS NULL OR source_origin=''")
    )
    conn.execute("UPDATE sources SET created_at=CURRENT_TIMESTAMP WHERE created_at IS NULL OR created_at=''")
    # Feed and playlist rows are never local author subscriptions (repairs an early version of this step).
    conn.execute(
        "UPDATE sources SET source_origin=CASE source_type WHEN 'feed' THEN 'account' ELSE 'playlist' END "
        "WHERE source_type IN ('feed', 'playlist')"
    )


def _add_site(conn: sqlite3.Connection) -> None:
    """Make the site (``tv`` / ``ai``) part of a source's identity.

    SQLite cannot change a UNIQUE constraint in place, so an older table is
    copied into one with the new constraint, keeping its ids (items refer to
    them).  Sources made before were all on iwara.tv.
    """
    existing = columns(conn, "sources")
    if "site" in existing:
        return
    conn.execute("DROP TABLE IF EXISTS sources_with_site")
    conn.execute(f"CREATE TABLE sources_with_site ({TABLE_BODY})")
    shared = ", ".join(c for c in existing if c in columns(conn, "sources_with_site"))
    conn.execute(f"INSERT INTO sources_with_site ({shared}) SELECT {shared} FROM sources")
    conn.execute("DROP TABLE sources")
    conn.execute("ALTER TABLE sources_with_site RENAME TO sources")
