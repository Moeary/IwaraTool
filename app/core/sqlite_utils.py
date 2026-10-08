"""Shared SQLite connection factory.

History, subscriptions and the UI all hit the same database files from
different threads, so every connection waits for locks instead of failing
with ``database is locked`` and the files run in WAL mode.
"""
from __future__ import annotations

import sqlite3

BUSY_TIMEOUT_SECONDS = 30.0


def connect(db_path: str) -> sqlite3.Connection:
    """Open a connection with a generous busy timeout and WAL journaling."""
    conn = sqlite3.connect(db_path, timeout=BUSY_TIMEOUT_SECONDS)
    try:
        conn.execute(f"PRAGMA busy_timeout={int(BUSY_TIMEOUT_SECONDS * 1000)}")
        # WAL persists in the database file; re-asserting it is cheap and
        # covers files created before this change.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
    except sqlite3.DatabaseError:
        # e.g. read-only media or a WAL-incapable filesystem: keep defaults.
        pass
    return conn
