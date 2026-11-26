"""Version 1: the v1.2.3 schema, the baseline every later step builds on.

A file at version 0 is new or comes from v1.2.3 or earlier (which recorded no
version).  New files get the v1.2.3 tables.  Older files are caught up the way
those releases did it, table by table (``baseline/``): missing columns are
added and filled, and ``sources`` is rebuilt once to make the site part of a
subscription's identity.  Every part checks what is already there, so a file
that already has the v1.2.3 shape is left untouched.
"""
from __future__ import annotations

import sqlite3

from .baseline import downloaded, items, sources

VERSION = 1
NAME = "baseline: the v1.2.3 schema"


def upgrade(conn: sqlite3.Connection) -> None:
    downloaded.create_or_catch_up(conn)
    sources.create_or_catch_up(conn)
    items.create_or_catch_up(conn)
