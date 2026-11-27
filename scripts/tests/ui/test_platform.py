"""Platform capabilities: what phones hide, and the dev override."""

from __future__ import annotations

import importlib.util
import os
import unittest
from types import SimpleNamespace
from unittest import mock

HAS_FLET = importlib.util.find_spec("flet") is not None


class CapabilitiesTests(unittest.TestCase):
    def test_phones_hide_desktop_features(self):
        from app.ui.platform.capabilities import capabilities

        phone = capabilities(SimpleNamespace(platform="android"))
        self.assertTrue(phone.mobile)
        self.assertTrue(phone.keep_awake)
        for name in ("repair", "tools", "open_folder", "local_aria2", "player_command", "keyboard_shortcuts", "window"):
            self.assertFalse(getattr(phone, name), name)

    def test_desktops_have_everything_but_the_wakelock(self):
        from app.ui.platform.capabilities import capabilities

        for platform in ("macos", "windows", "linux", ""):
            caps = capabilities(SimpleNamespace(platform=platform))
            self.assertTrue(caps.desktop and caps.repair and caps.tools and caps.open_folder and caps.local_aria2)
            self.assertFalse(caps.keep_awake)

    def test_override_variable(self):
        from app.ui.platform import capabilities as module

        with mock.patch.dict(os.environ, {module.OVERRIDE_VARIABLE: " Android "}):
            self.assertEqual(module.platform_override(), "android")
        with mock.patch.dict(os.environ, {module.OVERRIDE_VARIABLE: ""}):
            self.assertEqual(module.platform_override(), "")


class RouteGuardTests(unittest.TestCase):
    def test_desktop_only_paths_are_refused_on_phones(self):
        from app.ui import routes

        self.assertFalse(routes.allowed("/repair", desktop=False))
        self.assertFalse(routes.allowed("/tools/covers", desktop=False))
        self.assertTrue(routes.allowed("/tools/covers", desktop=True))
        self.assertTrue(routes.allowed("/library/123", desktop=False))
        self.assertTrue(routes.allowed("/detail/video/abc", desktop=False))


class VideoActionTests(unittest.TestCase):
    def test_folder_entries_can_be_left_out(self):
        from app.presentation.media import video_actions as va

        location = SimpleNamespace(local_path="/v/a.mp4", is_remote=False, remote_path="",
                                   playable_target="/v/a.mp4", exists=True)
        with mock.patch.object(va, "play_label", lambda *_a, **_k: "Play"):
            ids = [a.id for a in va.video_actions(location, open_folder=False)]
            self.assertNotIn(va.OPEN_FOLDER, ids)
            self.assertIn(va.COPY_PATH, ids)
            self.assertIn(va.OPEN_FOLDER, [a.id for a in va.video_actions(location)])


@unittest.skipUnless(HAS_FLET, "Flet is not installed in this environment")
class PlatformNameTests(unittest.TestCase):
    def test_override_wins_over_the_page(self):
        import flet as ft

        from app.ui.shell import platform_name

        page = SimpleNamespace(platform=ft.PagePlatform.MACOS)
        with mock.patch.dict(os.environ, {"IWARATOOL_PLATFORM": "android"}):
            self.assertEqual(platform_name(page), "android")
        with mock.patch.dict(os.environ, {"IWARATOOL_PLATFORM": ""}):
            self.assertEqual(platform_name(page), "macos")


@unittest.skipUnless(HAS_FLET, "Flet is not installed in this environment")
class PhoneLifecycleTests(unittest.TestCase):
    def _page(self, platform):
        import flet as ft

        confirmed = []

        class View:
            can_pop = True
            on_confirm_pop = None

            async def confirm_pop(self, should_pop):
                confirmed.append(should_pop)

        return SimpleNamespace(platform=platform, views=[View()], on_app_lifecycle_state_change=None), confirmed, ft

    def test_back_steps_inside_the_app_and_leaves_at_the_start(self):
        import asyncio

        import flet as ft

        from app.ui.lifecycle import install_back_handler

        page, confirmed, _ = self._page(ft.PagePlatform.ANDROID)
        steps = iter([True, False])
        install_back_handler(page, lambda: next(steps))
        view = page.views[0]
        self.assertFalse(view.can_pop)
        asyncio.run(view.on_confirm_pop(None))
        asyncio.run(view.on_confirm_pop(None))
        self.assertEqual(confirmed, [False, True])

    def test_desktop_keeps_the_default_pop(self):
        import flet as ft

        from app.ui.lifecycle import install_back_handler, install_pause_handler

        page, _confirmed, _ = self._page(ft.PagePlatform.MACOS)
        install_back_handler(page, lambda: True)
        install_pause_handler(page)
        self.assertTrue(page.views[0].can_pop)
        self.assertIsNone(page.on_app_lifecycle_state_change)

    def test_pause_saves_without_stopping(self):
        import asyncio

        import flet as ft

        from app.ui.lifecycle import install_pause_handler

        page, _confirmed, _ = self._page(ft.PagePlatform.ANDROID)
        resumed = []

        async def on_resume():
            resumed.append(True)

        install_pause_handler(page, on_resume=on_resume)
        with mock.patch("app.bootstrap.checkpoint") as checkpoint:
            asyncio.run(page.on_app_lifecycle_state_change(SimpleNamespace(state=ft.AppLifecycleState.RESUME)))
            checkpoint.assert_not_called()
            self.assertEqual(resumed, [True])
            asyncio.run(page.on_app_lifecycle_state_change(SimpleNamespace(state=ft.AppLifecycleState.PAUSE)))
            checkpoint.assert_called_once()


@unittest.skipUnless(HAS_FLET, "Flet is not installed in this environment")
class ShellBackTests(unittest.TestCase):
    def _shell(self, platform, destination):
        from app.ui.platform.capabilities import capabilities

        switched = []
        navigator = SimpleNamespace(back=lambda: False, switch=switched.append)
        state = SimpleNamespace(account_open=False, drawer_open=False, destination=destination)
        shell = SimpleNamespace(state=state, navigator=navigator, caps=capabilities(SimpleNamespace(platform=platform)),
                                secondary_page=lambda: False)
        return shell, switched

    def test_phones_go_home_before_leaving(self):
        from app.ui import routes
        from app.ui.shell import Shell

        shell, switched = self._shell("android", routes.SEARCH)
        self.assertTrue(Shell.back(shell))
        self.assertEqual(switched, [routes.HOME])
        shell, switched = self._shell("android", routes.HOME)
        self.assertFalse(Shell.back(shell))

    def test_back_closes_the_drawer_first(self):
        from app.ui import routes
        from app.ui.shell import Shell

        shell, switched = self._shell("android", routes.SEARCH)
        shell.state.drawer_open = True
        self.assertTrue(Shell.back(shell))
        self.assertFalse(shell.state.drawer_open)
        self.assertEqual(switched, [])

    def test_desktop_back_stops_at_the_destination_root(self):
        from app.ui import routes
        from app.ui.shell import Shell

        shell, switched = self._shell("macos", routes.SEARCH)
        self.assertFalse(Shell.back(shell))
        self.assertEqual(switched, [])


@unittest.skipUnless(HAS_FLET, "Flet is not installed in this environment")
class DrawerTests(unittest.TestCase):
    def test_drawer_leaves_a_strip_of_scrim(self):
        from app.ui.components.nav_drawer import MAX_WIDTH, drawer_width

        self.assertEqual(drawer_width(320), 264)  # 56 dp of scrim stays visible
        self.assertEqual(drawer_width(1000), MAX_WIDTH)


@unittest.skipUnless(HAS_FLET, "Flet is not installed in this environment")
class PhoneStorageTests(unittest.TestCase):
    def test_external_directory_preferred(self):
        from pathlib import Path

        from app.ui.platform import storage

        self.assertEqual(storage.phone_download_dir("/sdcard/Android/data/x/files"),
                         Path("/sdcard/Android/data/x/files") / storage.DOWNLOAD_FOLDER)
        self.assertEqual(storage.phone_download_dir(None).name, storage.DOWNLOAD_FOLDER)

    def test_only_the_untouched_default_moves(self):
        import asyncio
        import tempfile

        import flet as ft

        from app.ui.platform import storage

        external = tempfile.mkdtemp()
        config = SimpleNamespace(download_dir="/chosen/by/user")  # never the real settings file

        class Paths:
            async def get_external_storage_directory(self):
                return external

        def run(platform):
            asyncio.run(storage.prepare_phone_storage(SimpleNamespace(platform=platform)))

        with mock.patch.object(ft, "StoragePaths", Paths), mock.patch.object(storage, "app_config", config), \
                mock.patch("app.core.download.manager.download_manager.apply_config"):
            run(ft.PagePlatform.ANDROID)
            self.assertEqual(config.download_dir, "/chosen/by/user")
            with mock.patch.object(storage, "using_default_folder", lambda: True):
                run(ft.PagePlatform.MACOS)
                self.assertEqual(config.download_dir, "/chosen/by/user")
                run(ft.PagePlatform.ANDROID)
            self.assertEqual(config.download_dir, str(storage.phone_download_dir(external)))

if __name__ == "__main__":
    unittest.main()
