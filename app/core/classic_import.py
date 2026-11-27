"""Bring the data of IwaraTool v1.2 or earlier (the PySide6 "Classic" build) over.

Since v1.3.0 the app uses the same data folder as those versions, so this is
only needed when it runs from another place (on Windows / Linux, a program
folder other than the old one).  Importing makes a backup of the old folder (``core.backup``: settings, history, queue, rules, subscriptions;
never credentials or caches) and stages it as a restore, applied at the next
start exactly like restoring a backup file (the current files are kept under
``backup_before_restore``).

Where the Classic data lives:

- macOS: ``~/Library/Application Support/IwaraTool`` (found automatically);
- Windows / Linux: a ``data`` folder next to the Classic program, which the
  user picks (the program folder or the ``data`` folder itself).

This module must stay importable before ``app.config`` (no Qt, no config).
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from . import backup

MARKER = "config.ini"


def _has_data(folder: Path) -> bool:
    return (folder / MARKER).is_file()


def classic_data_dir(*, platform: str | None = None, home: Path | None = None, own: str = "") -> str:
    """The Classic data folder when it can be found without asking, else "".

    ``own``: the app's data folder.  Since v1.3.0 that is the Classic folder
    itself, so there is nothing to import from it.
    """
    platform = platform or sys.platform
    if platform != "darwin":
        return ""
    folder = (home or Path.home()) / "Library" / "Application Support" / "IwaraTool"
    if own and os.path.realpath(folder) == os.path.realpath(own):
        return ""
    return str(folder) if _has_data(folder) else ""


def find_data_dir(chosen: str) -> str:
    """The data folder for a picked folder: itself, or its ``data`` subfolder; "" when neither."""
    if not chosen:
        return ""
    folder = Path(chosen)
    for candidate in (folder, folder / "data"):
        if _has_data(candidate):
            return str(candidate)
    return ""


def stage_import(source_dir: str, data_dir: str) -> list[str]:
    """Stage the Classic data as a restore of ``data_dir``; the staged file names."""
    if not source_dir or not _has_data(Path(source_dir)):
        raise ValueError(f"{source_dir!r} is not an IwaraTool data folder")
    if os.path.realpath(source_dir) == os.path.realpath(data_dir):
        raise ValueError("the Classic data folder is this build's own data folder")
    with tempfile.TemporaryDirectory(prefix="iwaratool-classic-") as tmp:
        archive = os.path.join(tmp, "classic.zip")
        backup.create_backup(source_dir, archive)
        return backup.stage_restore(archive, data_dir)
