"""Desktop self-check of a packaged build (``build_flet.py`` smoke test, ``IWARATOOL_SMOKE_DESKTOP=1``).

Runs inside the packaged app, against its temporary smoke-test data folder:

- close to tray / Dock: a close request with "keep running" on must make the
  native window report HIDE (tray) or MINIMIZE (Dock), or report SHOW /
  RESTORE when shown again (events come from the Flutter client, so they
  prove the window really changed);
- the Windows tray icon is running;
- launch at login: the entry for this executable is written, read back and
  removed (macOS / Linux in a temporary home; Windows in the real Run key, left
  as it was);
- a system notification is accepted by the OS command.

Returns a report; ``failures`` lists what did not work.
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import flet as ft

from ..config import app_config
from ..core import login_items, system_notify
from .platform import tray

WAIT = 8.0
HIDDEN = {ft.WindowEventType.HIDE, ft.WindowEventType.MINIMIZE}
# SHOW / RESTORE only follow a hidden / minimized window, so they also prove the close step
# (on macOS the client reports RESTORE but not MINIMIZE for a minimize the app asked for).
SHOWN = {ft.WindowEventType.SHOW, ft.WindowEventType.RESTORE}


async def _wait_for(events: list, wanted: set, start: int, wait: float = WAIT) -> str:
    deadline = asyncio.get_running_loop().time() + wait
    while asyncio.get_running_loop().time() < deadline:
        for event in events[start:]:
            if event in wanted:
                return event.value
        await asyncio.sleep(0.1)
    return ""


async def _window(page: ft.Page, report: dict) -> None:
    app_config.minimize_to_tray = True  # the smoke test's own temporary settings
    tray.sync(page)
    service = tray.service(page)
    report["tray_running"] = service.running
    if sys.platform == "win32" and not service.running:
        report["failures"].append("the tray icon did not start")
    service.hint_shown = True  # the notification is checked on its own below
    events: list = []
    handler = page.window.on_event

    async def recording(event) -> None:
        events.append(event.type)
        if handler is not None:
            await handler(event)

    page.window.on_event = recording
    page.update()
    await asyncio.sleep(1.0)  # let the window settle (initial FOCUS / SHOW)
    start = len(events)
    await recording(SimpleNamespace(type=ft.WindowEventType.CLOSE))  # what the red button / X sends
    report["after_close"] = await _wait_for(events, HIDDEN, start, wait=3.0)
    start = len(events)
    await service.show_window()
    report["after_show"] = await _wait_for(events, SHOWN, start)
    if not report["after_close"] and not report["after_show"]:
        report["failures"].append("closing with keep-running on did not hide or minimize the window")
    elif not report["after_show"]:
        report["failures"].append("the window did not come back")
    report["window_events"] = [e.value for e in events]
    service.stop()
    page.window.on_event = handler


def _login_item(report: dict) -> None:
    if not login_items.is_supported():
        return
    args = login_items.launch_arguments()
    report["login_command"] = args
    target = args[-1] if sys.platform == "darwin" else args[0]  # the bundle `open -a` starts, or the exe
    if not Path(target).exists():
        report["failures"].append(f"launch-at-login target {target} does not exist")
    if sys.platform == "win32":
        was = login_items.is_enabled()
        login_items.set_enabled(True)
        enabled = login_items.is_enabled()
        if not was:
            login_items.set_enabled(False)
        report["login_item"] = enabled and (was or not login_items.is_enabled())
    else:
        with tempfile.TemporaryDirectory() as home:
            login_items.set_enabled(True, home=Path(home), args=args)
            enabled = login_items.is_enabled(home=Path(home))
            login_items.set_enabled(False, home=Path(home))
            report["login_item"] = enabled and not login_items.is_enabled(home=Path(home))
    if not report["login_item"]:
        report["failures"].append("the launch-at-login entry could not be written, read and removed")


async def run(page: ft.Page) -> dict:
    report: dict = {"failures": []}
    try:
        await _window(page, report)
    except Exception as exc:  # report, never hang the smoke test
        report["failures"].append(f"window check: {exc!r}")
    try:
        await asyncio.to_thread(_login_item, report)
    except Exception as exc:
        report["failures"].append(f"login item check: {exc!r}")
    report["notification"] = await asyncio.to_thread(
        system_notify.notify, "IwaraTool", "Packaged build self-check")
    if not report["notification"] and sys.platform in ("win32", "darwin"):
        report["failures"].append("the system notification command failed")
    return report
