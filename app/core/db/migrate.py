"""Bring ``history.db`` up to the current schema, one numbered step at a time.

The file's schema version is SQLite's ``PRAGMA user_version``.  Every step in
``migrations`` moves it from ``version - 1`` to ``version`` and runs in its own
transaction together with the version bump, so an interrupted upgrade resumes
at the step that did not finish.

- Version 0 is a file no versioned build has touched: a new file, or one from
  v1.2.3 or earlier, which never recorded a version.
- Version 1 (the baseline) is the v1.2.3 schema.  Its step creates what is
  missing and catches up files from any older release, so every later step can
  assume the v1.2.3 tables.
- Each later step makes one small change in a module of its own
  (``migrations/v0002_*.py`` …), listed in ``migrations.STEPS``.

A file written by a newer build (a version above the latest step) is left as
it is: steps only add, so this build can still read it.
"""
from __future__ import annotations

import sqlite3
import threading

from ...logging_setup import get_logger
from .migrations import STEPS, Step

logger = get_logger(__name__)

_done: set[str] = set()  # files already brought up to date by this process
_done_lock = threading.Lock()


def latest_version(steps: tuple[Step, ...] = STEPS) -> int:
    return steps[-1].version if steps else 0


def schema_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def check_steps(steps: tuple[Step, ...]) -> None:
    """Steps must be numbered 1, 2, 3 … with no gap or repeat."""
    for expected, step in enumerate(steps, start=1):
        if step.version != expected:
            raise ValueError(f"migration {step.name!r} has version {step.version}, expected {expected}")


def migrate(conn: sqlite3.Connection, steps: tuple[Step, ...] = STEPS) -> int:
    """Apply the steps the file has not had yet; returns the file's version afterwards."""
    check_steps(steps)
    current = schema_version(conn)
    target = latest_version(steps)
    if current > target:
        logger.warning("history.db is at schema version %s, newer than this build (%s); leaving it as it is",
                       current, target)
        return current
    for step in steps[current:]:
        conn.commit()  # each step starts its own transaction
        try:
            conn.execute("BEGIN IMMEDIATE")  # one writer: another thread or process waits here
            # Another connection may have upgraded the file while this one waited for the lock.
            if schema_version(conn) >= step.version:
                conn.rollback()
                continue
            step.upgrade(conn)
            conn.execute(f"PRAGMA user_version={int(step.version)}")
            conn.commit()
        except Exception:
            conn.rollback()
            logger.exception("history.db migration %s (%s) failed", step.version, step.name)
            raise
        logger.info("history.db migrated to schema version %s (%s)", step.version, step.name)
    return schema_version(conn)


def ensure_current(conn: sqlite3.Connection, db_path: str) -> None:
    """``migrate`` once per file and process; afterwards only a cheap version check.

    The file can be deleted or replaced while the app runs (a restore, a user
    removing it), so the version is read again rather than trusted.
    """
    with _done_lock:
        known = db_path in _done
    if known and schema_version(conn) == latest_version():
        return
    migrate(conn)
    with _done_lock:
        _done.add(db_path)
