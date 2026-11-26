"""The parts of the baseline step (version 1), one per table, as v1.2.3 left them."""
from __future__ import annotations

import sqlite3


def columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def add_missing(conn: sqlite3.Connection, table: str, required: dict[str, str]) -> set[str]:
    """Add the columns the table lacks; returns the names added."""
    existing = columns(conn, table)
    added = set()
    for name, ddl in required.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
            added.add(name)
    return added
