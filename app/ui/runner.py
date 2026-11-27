"""Prepare the environment and start the app.

``prepare_environment`` runs before anything else from ``app`` is imported and
picks the data folder (the one v1.2 and earlier used, so an update keeps
settings, history, queue and subscriptions):

- macOS: ``~/Library/Application Support/IwaraTool`` (``paths.py`` decides);
- Windows / Linux: ``data`` and ``download`` beside the program, i.e. the
  packaged app's executable (Python's own ``sys.argv[0]`` points inside the
  bundle there) or the project folder for a source run;
- phones: the app storage folder Flet provides.

The second concern is the desktop client in development.

In development ``flet run``/``ft.run`` launch the desktop client, and
``flet_desktop`` first looks for "a client from a previous ``flet build``" in
``<cwd>/build/macos/*.app`` or ``<cwd>/build/windows/*.exe``.  Those folders
may hold an old build of this repository (the Nuitka builds of v1.2 used
them), so starting from the project root could launch that instead.

``run`` therefore switches to a neutral working directory when such a folder
exists, after turning the relative paths the app reads from the environment
into absolute ones.  A packaged app is its own client and never takes this
path.
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

PATH_VARIABLES = ("IWARATOOL_DATA_DIR", "IWARATOOL_DOWNLOAD_DIR")


def is_packaged(environ=os.environ) -> bool:
    """True inside an app made by ``flet build`` (its runtime sets ``FLET_PLATFORM``)."""

    return bool(environ.get("FLET_PLATFORM"))


def host_executable() -> Path | None:
    """The packaged app's own executable on Windows / Linux (the program folder holds its data)."""
    try:
        if sys.platform == "win32":
            import ctypes

            buffer = ctypes.create_unicode_buffer(32768)
            if ctypes.windll.kernel32.GetModuleFileNameW(None, buffer, len(buffer)):
                return Path(buffer.value)
        elif sys.platform.startswith("linux"):
            return Path(os.readlink("/proc/self/exe"))
    except (OSError, AttributeError, ValueError):
        _log.warning("Could not find the program folder", exc_info=True)
    return None


MOBILE_PLATFORMS = ("android", "ios")


def prepare_environment(project_root: Path, environ=os.environ) -> None:
    """Set the variables the core reads at import time; call before importing ``app.config``."""

    if environ.get("IWARATOOL_DATA_DIR"):
        return
    platform = environ.get("FLET_PLATFORM", "").strip().lower()
    if platform in MOBILE_PLATFORMS:
        storage = environ.get("FLET_APP_STORAGE_DATA", "").strip()
        if storage:
            environ["IWARATOOL_DATA_DIR"] = str(Path(storage) / "IwaraTool")
        return
    if sys.platform == "darwin":
        return  # app/paths.py: ~/Library/Application Support/IwaraTool
    program = host_executable() if platform else None
    folder = program.parent if program else project_root
    environ["IWARATOOL_DATA_DIR"] = str(folder / "data")
    environ.setdefault("IWARATOOL_DOWNLOAD_DIR", str(folder / "download"))


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
