"""Version 2: index the download records by date.

The library and the duplicate checks list every record newest first
(``ORDER BY downloaded_at DESC``); with tens of thousands of records the index
spares a full sort each time.  Older builds ignore it.
"""
from __future__ import annotations

import sqlite3

VERSION = 2
NAME = "index download records by date"


def upgrade(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE INDEX IF NOT EXISTS downloaded_by_date ON downloaded(downloaded_at DESC)")
