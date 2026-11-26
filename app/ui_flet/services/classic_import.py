"""Import the PySide6 ("Classic") build's data (``core/classic_import.py``).

``import_classic`` is the Settings action (find or pick the folder, confirm,
stage the restore); ``offer_classic_import`` is the one-time prompt on a
desktop start when the Classic data can be found without asking (macOS).
"""
from __future__ import annotations

import asyncio

import flet as ft

from ...config import app_config
from ...core import backup, classic_import
from ...logging_setup import get_logger
from ...presentation.settings import values as sv
from ...presentation.settings.texts import stext
from .dialogs import confirm
from .notify import notice

logger = get_logger(__name__)

OFFERED_KEY = "classic_import_offered"


async def import_classic(page: ft.Page, source: str = "") -> bool:
    """Stage the Classic data as a restore; True when staged (a restart applies it)."""
    source = source or await asyncio.to_thread(classic_import.classic_data_dir)
    if not source:
        chosen = await ft.FilePicker().get_directory_path(dialog_title=stext("choose_classic_folder"))
        if not chosen:
            return False
        source = await asyncio.to_thread(classic_import.find_data_dir, chosen)
        if not source:
            notice(page, "error", stext("failed"), stext("no_iwaratool_data_in_folder"), duration_ms=6000)
            return False
    if not await confirm(page, stext("import_classic_data"), stext("import_classic_replaces"),
                         informative=f"{source}\n{stext('import_classic_login_again')}",
                         confirm_label=stext("stage_restore")):
        return False
    try:
        staged = await asyncio.to_thread(classic_import.stage_import, source, app_config.app_data_dir)
    except Exception as exc:
        logger.exception("Classic import failed")
        notice(page, "error", stext("failed"), str(exc), duration_ms=6000)
        return False
    notice(page, "success", stext("done"), sv.restore_staged_text(len(staged)), duration_ms=8000)
    return True


async def offer_classic_import(page: ft.Page) -> None:
    """Once per data folder: Classic data was found, offer to bring it over."""
    if app_config.get_ui_value(OFFERED_KEY, False) in (True, "true", "1"):
        return
    data_dir = app_config.app_data_dir
    source = await asyncio.to_thread(classic_import.classic_data_dir)
    if not source or await asyncio.to_thread(backup.has_pending_restore, data_dir):
        return
    app_config.set_ui_value(OFFERED_KEY, True)
    notice(page, "info", stext("classic_data_found"), stext("import_classic_offer"),
           action=stext("import"), on_action=lambda: page.run_task(import_classic, page, source),
           duration_ms=15000)
