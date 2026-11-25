"""Keep running when the window is closed (the classic window's "minimize to tray").

Windows: a tray icon (``tray_windows.py``); closing hides the window, the icon
shows it again or quits.  macOS: closing minimizes to the Dock, its usual
place for a running app.  Only while Settings › System › "keep running" is on.
"""
from __future__ import annotations

import asyncio
import contextvars
import sys
from pathlib import Path

import flet as ft

from ...config import app_config
from ...i18n import tr
from ...logging_setup import get_logger
from . import tray_windows

logger = get_logger(__name__)

ICON = Path(__file__).resolve().parents[2] / "icon.ico"
_TRAYS: dict[int, "TrayService"] = {}


def available(platform: str = sys.platform) -> bool:
    """Closing can keep the app running: a tray on Windows, the Dock on macOS."""
    return platform in ("win32", "darwin")


def keep_running_label() -> str:
    if sys.platform == "darwin":
        return tr("Keep running in the Dock when the window is closed", "关闭窗口时最小化到程序坞继续运行",
                  "ウィンドウを閉じても Dock で動作を続ける")
    return tr("Keep running in the system tray when the window is closed", "关闭窗口时最小化到系统托盘继续运行",
              "ウィンドウを閉じてもシステムトレイで動作を続ける")


class TrayService:
    def __init__(self, page: ft.Page):
        self.page = page
        self.loop = asyncio.get_running_loop()
        self.context = contextvars.copy_context()
        self.tray: tray_windows.WindowsTray | None = None
        self.hint_shown = False

    def _on_loop(self, coroutine_function) -> None:
        def call() -> None:
            self.page.run_task(coroutine_function)

        self.loop.call_soon_threadsafe(call, context=self.context.copy())

    def start(self) -> bool:
        if self.tray is not None and self.tray.running:
            return True
        if not tray_windows.is_supported():
            return False
        self.tray = tray_windows.WindowsTray(
            tooltip="IwaraTool", icon_path=str(ICON) if ICON.is_file() else "",
            show_label=tr("Show IwaraTool", "显示 IwaraTool", "IwaraTool を表示"),
            exit_label=tr("Exit", "退出", "終了"),
            on_show=lambda: self._on_loop(self.show_window),
            on_exit=lambda: self._on_loop(self.quit),
        )
        started = self.tray.start()
        if not started:
            logger.warning("The tray icon could not be added")
            self.tray = None
        return started

    def stop(self) -> None:
        if self.tray is not None:
            self.tray.stop()
            self.tray = None

    @property
    def running(self) -> bool:
        return self.tray is not None and self.tray.running

    async def hide_window(self) -> None:
        """Close pressed: out of sight, still downloading."""
        if self.running:
            self.page.window.visible = False
            self.page.window.skip_task_bar = True
        else:
            self.page.window.minimized = True  # macOS Dock, or a tray that failed
        self.page.update()
        if not self.hint_shown:
            self.hint_shown = True
            from ...core import system_notify

            where = tr("in the system tray", "在系统托盘中", "システムトレイで") if self.running else \
                tr("in the Dock", "在程序坞中", "Dock で")
            await asyncio.to_thread(system_notify.notify, "IwaraTool", tr(
                f"Still running {where}; downloads continue.", f"仍{where}运行，下载会继续。", f"{where}動作中です。ダウンロードは続きます。"))

    async def show_window(self) -> None:
        window = self.page.window
        window.skip_task_bar = False
        window.visible = True
        window.minimized = False
        self.page.update()
        await window.to_front()

    async def quit(self) -> None:
        from ..lifecycle import shutdown

        await self.show_window()
        self.stop()
        await shutdown(self.page)


def service(page: ft.Page) -> TrayService:
    found = _TRAYS.get(id(page))
    if found is None:
        found = _TRAYS[id(page)] = TrayService(page)
    return found


def sync(page: ft.Page) -> None:
    """Start or stop the tray icon to match the setting (Windows)."""
    tray = service(page)
    if app_config.minimize_to_tray and available():
        tray.start()
    else:
        tray.stop()
