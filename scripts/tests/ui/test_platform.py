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


class FakeConfig(SimpleNamespace):
    """Never the real settings file."""

    def __init__(self, **values):
        super().__init__(image_cache_dir="", **values)
        self.ui: dict = {}

    def get_ui_value(self, key, default=""):
        return self.ui.get(key, default)

    def set_ui_value(self, key, value):
        self.ui[key] = value


@unittest.skipUnless(HAS_FLET, "Flet is not installed in this environment")
class PhoneStorageTests(unittest.TestCase):
    def test_shared_download_folder(self):
        from pathlib import Path

        from app.ui.platform import storage

        self.assertEqual(storage.public_download_dir("/storage/emulated/0/Android/data/x/files"),
                         Path("/storage/emulated/0/Download/IwaraTool"))
        self.assertEqual(storage.public_download_dir(None), storage.DEFAULT_SHARED_ROOT / "Download" / "IwaraTool")
        self.assertEqual(storage.phone_download_dir("/sdcard/Android/data/x/files"),
                         Path("/sdcard/Android/data/x/files") / storage.DOWNLOAD_FOLDER)

    def test_the_old_app_specific_folder_counts_as_default(self):
        from app.ui.platform import storage

        for folder, default in (("", True), ("/storage/emulated/0/Android/data/x/files/Downloads", True),
                                ("/storage/emulated/0/Android/data/x/files/Movies", False),
                                ("/storage/emulated/0/Movies/mine", False)):
            with mock.patch.object(storage, "app_config", FakeConfig(download_dir=folder)):
                self.assertEqual(storage.using_default_folder(), default, folder)

    def _prepare(self, platform, config, *, access: bool, public, external="/storage/emulated/0/Android/data/x/files"):
        import asyncio

        import flet as ft

        from app.ui.platform import storage

        class Paths:
            async def get_external_storage_directory(self):
                return external

        async def granted(_page):
            return access

        with mock.patch.object(ft, "StoragePaths", Paths), mock.patch.object(storage, "app_config", config), \
                mock.patch.object(storage, "storage_access", granted), \
                mock.patch.object(storage, "public_download_dir", lambda _external: public), \
                mock.patch("app.core.download.manager.download_manager.apply_config"):
            asyncio.run(storage.prepare_phone_storage(SimpleNamespace(platform=platform)))

    def test_downloads_and_cache_move_to_the_shared_folder(self):
        import tempfile
        from pathlib import Path

        import flet as ft

        public = Path(tempfile.mkdtemp()) / "Download" / "IwaraTool"
        config = FakeConfig(download_dir="")
        self._prepare(ft.PagePlatform.MACOS, config, access=True, public=public)
        self.assertEqual((config.download_dir, config.image_cache_dir), ("", ""))
        self._prepare(ft.PagePlatform.ANDROID, config, access=True, public=public)
        self.assertEqual(config.download_dir, str(public))
        self.assertEqual(config.image_cache_dir, str(public / ".cache"))
        self.assertTrue((public / ".cache" / ".nomedia").is_file())

    def test_without_access_only_the_cache_stays_private(self):
        import tempfile
        from pathlib import Path

        import flet as ft

        public = Path(tempfile.mkdtemp()) / "Download" / "IwaraTool"
        config = FakeConfig(download_dir="/storage/emulated/0/Android/data/x/files/Downloads")
        self._prepare(ft.PagePlatform.ANDROID, config, access=False, public=public)
        self.assertEqual((config.download_dir, config.image_cache_dir), (str(public), ""))

    def test_a_chosen_folder_stays(self):
        import tempfile
        from pathlib import Path

        import flet as ft

        public = Path(tempfile.mkdtemp()) / "Download" / "IwaraTool"
        config = FakeConfig(download_dir="/storage/emulated/0/Movies/mine")
        self._prepare(ft.PagePlatform.ANDROID, config, access=True, public=public)
        self.assertEqual(config.download_dir, "/storage/emulated/0/Movies/mine")
        self.assertEqual(config.image_cache_dir, str(public / ".cache"))

    def test_unwritable_shared_folder_falls_back_to_the_app_folder(self):
        import tempfile
        from pathlib import Path

        import flet as ft

        from app.ui.platform import storage

        external = tempfile.mkdtemp()
        blocker = Path(tempfile.mkdtemp()) / "file"
        blocker.write_text("")
        config = FakeConfig(download_dir="")
        self._prepare(ft.PagePlatform.ANDROID, config, access=True, public=blocker / "IwaraTool", external=external)
        self.assertEqual(config.download_dir, str(storage.phone_download_dir(external)))
        self.assertEqual(config.image_cache_dir, "")

    def test_an_auto_chosen_folder_moves_when_it_stops_working(self):
        import tempfile
        from pathlib import Path

        import flet as ft

        from app.ui.platform import storage

        external = tempfile.mkdtemp()
        public = Path(tempfile.mkdtemp()) / "Download" / "IwaraTool"
        config = FakeConfig(download_dir="")
        self._prepare(ft.PagePlatform.ANDROID, config, access=True, public=public, external=external)
        self.assertEqual(config.download_dir, str(public))
        blocker = Path(tempfile.mkdtemp()) / "file"
        blocker.write_text("")  # access revoked: the shared folder cannot be written any more
        config.image_cache_dir = ""
        self._prepare(ft.PagePlatform.ANDROID, config, access=False, public=blocker / "IwaraTool", external=external)
        self.assertEqual(config.download_dir, str(storage.phone_download_dir(external)))


@unittest.skipUnless(HAS_FLET, "Flet is not installed in this environment")
class VideoFullscreenTests(unittest.TestCase):
    def test_layout_holds_still_while_a_video_is_fullscreen(self):
        from app.ui.shell import Shell

        applied: list[tuple[float, float]] = []
        later: list = []
        shell = SimpleNamespace(
            _video_fullscreen=False, _resize_handle=None, _pending_size=None,
            caps=SimpleNamespace(mobile=False),
            loop=SimpleNamespace(call_later=lambda _delay, callback: later.append(callback) or SimpleNamespace(cancel=lambda: None)),
        )
        shell.state = SimpleNamespace(layout=SimpleNamespace(width=400, height=800))
        shell._layout = lambda w, h: applied.append((w, h)) or SimpleNamespace(width=w, height=h)
        shell._sync_notice_frame = lambda: None
        shell._apply_size = lambda: Shell._apply_size(shell)
        Shell.set_video_fullscreen(shell, True)
        shell._pending_size = (800, 400)  # the phone turned
        Shell._apply_size(shell)
        self.assertEqual(applied, [])
        Shell.set_video_fullscreen(shell, False)
        self.assertEqual(len(later), 1)
        shell._pending_size = (400, 800)  # the size after the exit arrives before the settle delay
        later.pop()()
        self.assertEqual(applied, [], "the layout never left portrait")


class ImageCacheRootTests(unittest.TestCase):
    def test_root_follows_the_configured_cache_folder(self):
        import tempfile

        from app.config import app_config
        from app.core.image_cache import SearchImageCache, SubscriptionImageCache

        folder = tempfile.mkdtemp()
        cache, sub = SearchImageCache(), SubscriptionImageCache()
        app_config.image_cache_dir = folder
        try:
            self.assertEqual(cache.root, os.path.join(folder, "search"))
            self.assertEqual(sub.root, os.path.join(folder, "sub"))
        finally:
            app_config.image_cache_dir = ""
        self.assertEqual(SearchImageCache("/x/y").root, os.path.abspath("/x/y"))


if __name__ == "__main__":
    unittest.main()
