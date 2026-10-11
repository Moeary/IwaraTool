"""Writable application paths, separate from a macOS application bundle."""
from __future__ import annotations

import sys
from pathlib import Path


def app_root_dir() -> Path:
    return Path(sys.argv[0]).resolve().parent


def app_data_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "IwaraTool"
    return app_root_dir() / "data"


def default_download_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Downloads" / "IwaraTool"
    return app_root_dir() / "download"
