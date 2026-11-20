"""Writable application paths, separate from a macOS application bundle.

``IWARATOOL_DATA_DIR`` and ``IWARATOOL_DOWNLOAD_DIR`` override the data and
default download folders, for hosts that decide them (an Android app passes
its own storage) and for tests.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _override(name: str) -> Path | None:
    value = os.environ.get(name, "").strip()
    return Path(value).expanduser() if value else None


def app_root_dir() -> Path:
    return Path(sys.argv[0]).resolve().parent


def app_data_dir() -> Path:
    override = _override("IWARATOOL_DATA_DIR")
    if override is not None:
        return override
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "IwaraTool"
    return app_root_dir() / "data"


def default_download_dir() -> Path:
    override = _override("IWARATOOL_DOWNLOAD_DIR")
    if override is not None:
        return override
    if sys.platform == "darwin":
        return Path.home() / "Downloads" / "IwaraTool"
    return app_root_dir() / "download"
