"""Where a phone keeps downloads (docs/flet_ui_plan.md 8.2).

The core's default download folder sits next to the program, which on
Android is the unpacked app (replaced on update and invisible to the user).
Until the user picks a folder, downloads go to the app-specific external
directory (``Android/data/<package>/files``): no permission is needed, file
managers can reach it, and Android removes it with the app.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import flet as ft

from ...config import app_config
from ...logging_setup import get_logger
from ...paths import app_data_dir, default_download_dir

logger = get_logger(__name__)

DOWNLOAD_FOLDER = "Downloads"


def using_default_folder() -> bool:
    current = str(app_config.download_dir or "").strip()
    return not current or os.path.normcase(os.path.abspath(current)) == os.path.normcase(
        os.path.abspath(str(default_download_dir())))


def phone_download_dir(external: str | None) -> Path:
    """The folder for downloads: the app's external files directory, else its private data folder."""
    base = Path(external) if external else Path(app_data_dir())
    return base / DOWNLOAD_FOLDER


async def prepare_phone_storage(page: ft.Page) -> None:
    """Android: move the untouched default download folder somewhere that survives updates."""
    if page.platform != ft.PagePlatform.ANDROID or not using_default_folder():
        return
    external = None
    try:
        external = await ft.StoragePaths().get_external_storage_directory()
    except Exception:
        logger.warning("No external storage directory; downloads stay in the app's data folder", exc_info=True)
    target = phone_download_dir(external)
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.exception("Cannot create %s", target)
        return
    app_config.download_dir = str(target)
    from ...core.download.manager import download_manager

    await asyncio.to_thread(download_manager.apply_config)
