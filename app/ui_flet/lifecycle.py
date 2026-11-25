"""Start the app's services for the Flet interface and stop them when the window closes.

``app.bootstrap`` does the work; its calls block, so they run with
``asyncio.to_thread`` and the event loop stays free.  On desktop the native
close is intercepted (``prevent_close``) so the download queue and settings
are saved before the window goes away.
"""
from __future__ import annotations

import asyncio

import flet as ft

from .. import bootstrap
from ..i18n import tr
from ..logging_setup import get_logger
from .theme import DESKTOP_PLATFORMS

logger = get_logger(__name__)


async def start_app(page: ft.Page, *, services: bool = True) -> None:
    """``bootstrap.prepare`` (and ``start_services`` unless ``services`` is False) off the loop."""

    await asyncio.to_thread(bootstrap.prepare, frontend="flet")
    if services:
        await asyncio.to_thread(bootstrap.start_services)
    install_close_handler(page)


async def shutdown(page: ft.Page) -> None:
    """Stop services and write settings, then close the window (desktop)."""

    try:
        await asyncio.to_thread(bootstrap.stop, wait=True)
    except Exception:  # never keep the window open because saving failed
        logger.exception("Shutdown did not finish cleanly")
    if page.platform in DESKTOP_PLATFORMS:
        await page.window.destroy()


async def _keep_running(page: ft.Page) -> bool:
    """With downloads running, ask before quitting (there is no tray to keep them going)."""
    from ..core.download.manager import download_manager
    from ..presentation.downloads.task_statuses import badge_counts
    from .services.dialogs import confirm

    try:
        active, _staged = await asyncio.to_thread(badge_counts, download_manager)
    except Exception:
        logger.exception("Could not count running downloads")
        return False
    if not active:
        return False
    quit_now = await confirm(
        page,
        tr("Downloads are running", "仍有下载在进行", "ダウンロード中です"),
        tr(f"{active} task(s) are still downloading. Quitting stops them; the queue is kept for next time.",
           f"仍有 {active} 个任务在下载。退出会停止它们，下载队列会保留到下次启动。",
           f"{active} 件のタスクがダウンロード中です。終了すると停止しますが、キューは次回まで保持されます。"),
        confirm_label=tr("Quit", "退出", "終了"),
        cancel_label=tr("Minimize", "最小化", "最小化"),
        danger=True,
    )
    return not quit_now


def install_close_handler(page: ft.Page) -> None:
    if page.platform not in DESKTOP_PLATFORMS:
        return
    page.window.prevent_close = True

    async def on_window_event(event: ft.WindowEvent) -> None:
        if event.type == ft.WindowEventType.CLOSE:
            if await _keep_running(page):
                page.window.minimized = True
                return
            await shutdown(page)

    page.window.on_event = on_window_event
