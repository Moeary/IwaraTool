"""The numbered schema steps of ``history.db``, oldest first (see ``db/migrate.py``).

Adding a change to the schema:

1. add ``vNNNN_<what>.py`` beside these, with ``VERSION = NNNN`` (the next
   number), a one-line ``NAME`` and ``upgrade(conn)`` making only that change;
2. append it to ``STEPS`` below;
3. add a test in ``scripts/tests/core/test_db_migrations.py``.

A released step is never edited: a later step corrects it.  Steps run inside a
transaction, so they must not commit.  Modules are listed here rather than
discovered so packagers (Nuitka, ``flet build``) include every one.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from . import v0001_baseline, v0002_library_order_index


@dataclass(frozen=True)
class Step:
    version: int
    name: str
    upgrade: Callable[[sqlite3.Connection], None]


def _step(module) -> Step:
    return Step(module.VERSION, module.NAME, module.upgrade)


STEPS: tuple[Step, ...] = tuple(_step(m) for m in (
    v0001_baseline,
    v0002_library_order_index,
))
