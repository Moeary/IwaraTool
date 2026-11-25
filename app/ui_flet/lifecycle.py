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


def install_close_handler(page: ft.Page) -> None:
    if page.platform not in DESKTOP_PLATFORMS:
        return
    page.window.prevent_close = True

    async def on_window_event(event: ft.WindowEvent) -> None:
        if event.type == ft.WindowEventType.CLOSE:
            await shutdown(page)

    page.window.on_event = on_window_event
