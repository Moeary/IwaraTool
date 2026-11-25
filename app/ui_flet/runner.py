"""Start a Flet app from a source checkout without picking up the wrong client.

In development ``flet run``/``ft.run`` launch the desktop client, and
``flet_desktop`` first looks for "a client from a previous ``flet build``" in
``<cwd>/build/macos/*.app`` or ``<cwd>/build/windows/*.exe``.  In this
repository those folders hold the Nuitka build of the PySide6 interface, so
starting from the project root would launch the old app instead of Flet.

``run`` therefore switches to a neutral working directory when such a folder
exists, after turning the relative paths the app reads from the environment
into absolute ones.  A packaged Flet app is its own client and never takes
this path.
"""

from __future__ import annotations

import logging
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

PATH_VARIABLES = ("IWARATOOL_DATA_DIR", "IWARATOOL_DOWNLOAD_DIR")
_CLIENT_FOLDERS = (Path("build") / "macos", Path("build") / "windows", Path("build") / "linux")


def foreign_client_folder(cwd: Path) -> Path | None:
    """The ``build/<platform>`` folder ``flet_desktop`` would take a client from, if any."""

    for folder in _CLIENT_FOLDERS:
        candidate = cwd / folder
        if candidate.is_dir() and any(candidate.iterdir()):
            return candidate
    return None


def absolutize_path_variables(cwd: Path, environ: dict[str, str] | os._Environ = os.environ) -> None:
    for name in PATH_VARIABLES:
        value = environ.get(name, "").strip()
        if value:
            path = Path(value).expanduser()
            environ[name] = str(path if path.is_absolute() else (cwd / path).resolve())


def prepare_working_directory() -> Path:
    """Make the environment paths absolute and leave a folder with a foreign client."""

    cwd = Path.cwd()
    absolutize_path_variables(cwd)
    folder = foreign_client_folder(cwd)
    if folder is None:
        return cwd
    neutral = Path(tempfile.gettempdir()) / "iwaratool-flet-cwd"
    neutral.mkdir(exist_ok=True)
    os.chdir(neutral)
    _log.info("Started from %s, which holds %s; running Flet from %s", cwd, folder, neutral)
    return neutral


def run(main: Callable[[Any], Any], *, assets_dir: Path | None = None) -> None:
    import flet as ft

    prepare_working_directory()
    ft.run(main, assets_dir=str(assets_dir) if assets_dir else None)
