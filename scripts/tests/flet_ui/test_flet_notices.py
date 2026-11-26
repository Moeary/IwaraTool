"""Finished-download notices and the Windows tray icon (Flet build)."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

HAS_FLET = importlib.util.find_spec("flet") is not None


@unittest.skipUnless(HAS_FLET, "Flet is not installed in this environment")
class SystemNoticesTests(unittest.TestCase):
    def _notices(self, *, desktop=True, focused=True, enabled=True, notify_ok=True):
        from app.ui_flet.services import system_notices

        shown, system = [], []
        loop = asyncio.new_event_loop()
        self.addCleanup(loop.close)
        tasks = []
        page = SimpleNamespace(run_task=lambda fn, *a: tasks.append(fn(*a)))
        shell = SimpleNamespace(loop=loop, page=page, caps=SimpleNamespace(desktop=desktop))
        manager = SimpleNamespace(get_task=lambda task_id: SimpleNamespace(title=f"T{task_id}", video_id="", error_msg="x"))
        patches = [
            mock.patch.object(system_notices, "notice", lambda _p, _k, title, message, **_kw: shown.append((title, message))),
            mock.patch.object(system_notices, "window_focused", lambda _p: focused),
            mock.patch.object(system_notices.system_notify, "notify",
                              lambda title, message: system.append((title, message)) or notify_ok),
            # A stand-in: never the real settings file.
            mock.patch.object(system_notices, "app_config", SimpleNamespace(desktop_notifications_enabled=enabled)),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return system_notices.SystemNotices(shell, manager), loop, tasks, shown, system

    def _drain(self, loop, tasks):
        loop.run_until_complete(asyncio.sleep(0.7))
        while tasks:
            loop.run_until_complete(tasks.pop(0))

    def test_a_burst_becomes_one_notice_in_the_window(self):
        notices, loop, tasks, shown, system = self._notices()
        for task_id in ("1", "2", "3"):
            notices.on_task_status(task_id, "completed")
        notices.on_task_status("4", "failed")
        notices.on_task_status("5", "downloading")  # not terminal
        self._drain(loop, tasks)
        self.assertEqual(len(shown), 1)
        self.assertIn("3", shown[0][1])
        self.assertEqual(system, [])

    def test_background_window_uses_the_system(self):
        notices, loop, tasks, shown, system = self._notices(focused=False)
        notices.on_task_status("1", "completed")
        self._drain(loop, tasks)
        self.assertEqual(system, [(system[0][0], "T1")])
        self.assertEqual(shown, [])

    def test_disabled_or_failed_system_notice_falls_back(self):
        for kwargs in ({"enabled": False}, {"notify_ok": False}):
            notices, loop, tasks, shown, _system = self._notices(focused=False, **kwargs)
            notices.on_task_status("1", "completed")
            self._drain(loop, tasks)
            self.assertEqual(len(shown), 1, kwargs)

    def test_phones_stay_in_app(self):
        notices, loop, tasks, shown, system = self._notices(desktop=False, focused=False)
        loop.run_until_complete(notices.show("t", "m"))
        self.assertEqual((shown, system), ([("t", "m")], []))


@unittest.skipUnless(HAS_FLET, "Flet is not installed in this environment")
class ShowWindowTests(unittest.TestCase):
    def test_show_returns_when_the_client_never_raises_the_window(self):
        from app.ui_flet.platform import tray

        async def never() -> None:
            await asyncio.Event().wait()

        window = SimpleNamespace(skip_task_bar=True, visible=False, minimized=True, to_front=never)
        page = SimpleNamespace(window=window, update=lambda: None)
        async def show() -> None:
            await asyncio.wait_for(tray.TrayService(page).show_window(), 2)  # the service wants a running loop

        with mock.patch.object(tray, "RAISE_TIMEOUT", 0.05):
            asyncio.run(show())
        self.assertTrue(window.visible)
        self.assertFalse(window.minimized)
        self.assertFalse(window.skip_task_bar)


class TrayTests(unittest.TestCase):
    def test_not_started_off_windows(self):
        from app.ui_flet.platform import tray_windows

        tray = tray_windows.WindowsTray(tooltip="t", icon_path="", show_label="s", exit_label="e",
                                        on_show=lambda: None, on_exit=lambda: None)
        if sys.platform != "win32":
            self.assertFalse(tray.start())
            tray.stop()

    @unittest.skipUnless(sys.platform == "win32", "Windows tray icon")
    def test_windows_tray_starts_and_stops(self):
        from pathlib import Path

        from app.ui_flet.platform import tray_windows

        icon = Path(__file__).resolve().parents[3] / "app" / "icon.ico"
        tray = tray_windows.WindowsTray(tooltip="IwaraTool test", icon_path=str(icon), show_label="Show",
                                        exit_label="Exit", on_show=lambda: None, on_exit=lambda: None)
        started = tray.start()
        # A CI runner may have no shell to host the icon; the thread must still end cleanly.
        tray.stop()
        self.assertFalse(tray.running)
        print("tray icon added:", started)


if __name__ == "__main__":
    unittest.main()
