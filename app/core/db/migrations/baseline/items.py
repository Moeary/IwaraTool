"""``items``: the videos found in each subscription source."""
from __future__ import annotations

import sqlite3

from . import add_missing

CREATE = (
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

LATER_COLUMNS: dict[str, str] = {
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


def create_or_catch_up(conn: sqlite3.Connection) -> None:
    conn.execute(CREATE)
    add_missing(conn, "items", LATER_COLUMNS)
