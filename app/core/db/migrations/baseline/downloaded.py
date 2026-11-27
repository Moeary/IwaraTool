"""``downloaded``: one row per downloaded video (the library)."""
from __future__ import annotations

import sqlite3

from . import add_missing

CREATE = (
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
    "downloaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
    "watched_at TEXT DEFAULT '', "
    "storage TEXT DEFAULT 'local', "
    "remote_path TEXT DEFAULT ''"
    ")"
)

# Columns added over the releases; v0.1 had only video_id and downloaded_at.
LATER_COLUMNS: dict[str, str] = {
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
    "watched_at": "TEXT DEFAULT ''",  # last time the file was played; '' = never
    # 'local' (file_path on this machine) or 'remote' (remote_path on the aria2 host)
    "storage": "TEXT DEFAULT 'local'",
    "remote_path": "TEXT DEFAULT ''",
}


def create_or_catch_up(conn: sqlite3.Connection) -> None:
    conn.execute(CREATE)
    add_missing(conn, "downloaded", LATER_COLUMNS)
