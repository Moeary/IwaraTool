"""Open the subscription tables (they share ``history.db``; the schema lives in ``core/db``)."""

from __future__ import annotations

import sqlite3
from contextlib import closing

from ..db.migrate import ensure_current
from ..sqlite_utils import connect as _sqlite_connect


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return bool(row)


class StoreSchemaMixin:
    """Bring the database file up to the current schema (``db/migrate.py``)."""

    def _init_db(self):
        with closing(_sqlite_connect(self._db_path)) as conn:
            ensure_current(conn, self._db_path)
