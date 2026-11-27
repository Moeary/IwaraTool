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
from ..core import login_items
from ..i18n import tr
from ..logging_setup import get_logger
from ..config import app_config
from .platform import tray
from .platform.storage import prepare_phone_storage
from .theme import DESKTOP_PLATFORMS

logger = get_logger(__name__)


async def start_app(page: ft.Page, *, services: bool = True) -> None:
    """``bootstrap.prepare`` (and ``start_services`` unless ``services`` is False) off the loop."""

    await asyncio.to_thread(bootstrap.prepare, frontend="flet")
    await prepare_phone_storage(page)  # before the queue resumes into the download folder
    if services:
        await asyncio.to_thread(bootstrap.start_services)
        if page.platform in DESKTOP_PLATFORMS and login_items.is_supported() and login_items._packaged():
            try:  # launch at login keeps starting this copy (and replaces an entry v1.2 left)
                await asyncio.to_thread(login_items.follow_program)
            except OSError:
                logger.warning("Could not update the launch-at-login entry", exc_info=True)
    install_close_handler(page)
    if page.platform in DESKTOP_PLATFORMS:
        tray.sync(page)


SAVE_ON = frozenset({ft.AppLifecycleState.PAUSE, ft.AppLifecycleState.HIDE, ft.AppLifecycleState.DETACH})


def install_pause_handler(page: ft.Page, on_resume=None) -> None:
    """Phones: save the queue and settings whenever the app leaves the foreground (8.2).

    The system may end a paused app without notice, and there is no window
    close to wait for.  ``on_resume`` (a coroutine function) runs when it
    comes back.
    """
    if page.platform in DESKTOP_PLATFORMS:
        return

    async def on_lifecycle(event: ft.AppLifecycleStateChangeEvent) -> None:
        try:
            if event.state in SAVE_ON:
                await asyncio.to_thread(bootstrap.checkpoint)
            elif event.state == ft.AppLifecycleState.RESUME and on_resume is not None:
                await on_resume()
        except Exception:
            logger.exception("Handling %s failed", event.state)

    page.on_app_lifecycle_state_change = on_lifecycle


def install_back_handler(page: ft.Page, back) -> None:
    """The system back key / gesture steps back in the app; at the start it leaves (8.2).

    ``back`` is ``Shell.back`` (True when it went somewhere).  Dialogs and
    sheets are popped by Flutter before this runs.  The root view must not pop
    on its own, so it asks first (``can_pop=False`` + ``on_confirm_pop``).
    """
    if page.platform in DESKTOP_PLATFORMS or not page.views:
        return
    view = page.views[0]

    async def on_confirm_pop(_event) -> None:
        stepped = False
        try:
            stepped = bool(back())
        except Exception:
            logger.exception("Back navigation failed")
        await view.confirm_pop(not stepped)

    view.can_pop = False
    view.on_confirm_pop = on_confirm_pop


async def shutdown(page: ft.Page) -> None:
    """Stop services and write settings, then close the window (desktop)."""

    tray.service(page).stop()
    from .stores.app_state import mounted_shell

    shell = mounted_shell()
    if shell is not None:
        try:
            # Cancels repair, compression and cover work and writes library removals still
            # waiting for Undo (on the loop: it cancels the Undo timer; one short write).
            shell.close()
        except Exception:
            logger.exception("Closing the interface's background work failed")
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


_FOCUSED: dict[int, bool] = {}


def window_focused(page: ft.Page) -> bool:
    """Whether the desktop window has the focus (from its focus / blur events; True until one arrives)."""
    return _FOCUSED.get(id(page), True)


def install_close_handler(page: ft.Page) -> None:
    if page.platform not in DESKTOP_PLATFORMS:
        return
    page.window.prevent_close = True

    async def on_window_event(event: ft.WindowEvent) -> None:
        if event.type in (ft.WindowEventType.FOCUS, ft.WindowEventType.BLUR):
            _FOCUSED[id(page)] = event.type == ft.WindowEventType.FOCUS
            return
        if event.type == ft.WindowEventType.CLOSE:
            if app_config.minimize_to_tray and tray.available():
                await tray.service(page).hide_window()  # the tray icon or the Dock brings it back
                return
            if await _keep_running(page):
                page.window.minimized = True
                return
            await shutdown(page)

    page.window.on_event = on_window_event
