"""The tools store: ffmpeg lookup, probing, the compressor run and the cover grabs, with ffmpeg faked (no window)."""

from __future__ import annotations

import asyncio
import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HAS_FLET = importlib.util.find_spec("flet") is not None


@unittest.skipUnless(HAS_FLET, "Flet is not installed in this environment")
class ToolsStoreTests(unittest.TestCase):
    def setUp(self):
        from app.core.tools.ffmpeg import FfmpegInfo
        from scripts.tests.core.test_tools_presentation import media

        self.folder = Path(tempfile.mkdtemp())
        self.video = self.folder / "clip.webm"
        self.video.write_bytes(b"x")
        self.info = FfmpegInfo(Path("ffmpeg"), Path("ffprobe"), "7", encoders=frozenset({"libx264"}))
        self.media = media(path=self.video)
        self.ui: dict = {}
        self.notices: list = []
        patches = [
            mock.patch("app.ui_flet.stores.tools.locate", lambda _configured: self.info),
            mock.patch("app.ui_flet.stores.tools.probe", lambda _info, _path: self.media),
            mock.patch("app.ui_flet.stores.tools.notice", lambda _page, kind, title, content="": self.notices.append((kind, title))),
            mock.patch("app.ui_flet.stores.tools.app_config.get_ui_value", lambda key, default=None: self.ui.get(key, default)),
            mock.patch("app.ui_flet.stores.tools.app_config.set_ui_value", lambda key, value: self.ui.__setitem__(key, value)),
            mock.patch("app.presentation.tools.compress.app_config.get_ui_value", lambda key, default=None: self.ui.get(key, default)),
            mock.patch("app.presentation.tools.compress.app_config.set_ui_value", lambda key, value: self.ui.__setitem__(key, value)),
            mock.patch("app.presentation.tools.covers.app_config.get_ui_value", lambda key, default=None: self.ui.get(key, default)),
            mock.patch("app.presentation.tools.covers.app_config.set_ui_value", lambda key, value: self.ui.__setitem__(key, value)),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def _store(self, loop):
        from app.ui_flet.stores.tools import ToolsStore

        page = SimpleNamespace(run_task=lambda fn, *args: loop.create_task(fn(*args)))
        return ToolsStore(SimpleNamespace(loop=loop, page=page))

    def test_compress_flow(self):
        class FakeCompressor:
            def __init__(self, ffmpeg, media, plan, options, output):
                self.output, self.plan, self.stderr_tail = output, plan, ""

            def run(self, on_progress):
                on_progress("pass1", 1.0, 0.5)
                Path(self.output).write_bytes(b"mp4")
                return SimpleNamespace(output=self.output, size=3, elapsed=1.0, plan=self.plan)

            def cancel(self):
                pass

        async def run():
            store = self._store(asyncio.get_running_loop())
            store.load()
            await store.refresh_ffmpeg()
            self.assertTrue(store.compress_ready)
            await store.load_source(str(self.video))
            self.assertIsNotNone(store.verdict())
            self.assertTrue(store.state.output.endswith(".mp4"))
            self.assertTrue(store.can_start)
            store.set_form(limit="premium")
            self.assertIn("premium", self.ui["tools_compress_options_v1"])
            with mock.patch("app.ui_flet.stores.tools.Compressor", FakeCompressor):
                await store.start()
            self.assertEqual(store.state.progress, 1.0)
            self.assertTrue(store.state.last_output.endswith(".mp4"))
            self.assertEqual(self.notices[-1][0], "success")

        asyncio.run(run())

    def test_output_cannot_overwrite_the_source(self):
        async def run():
            store = self._store(asyncio.get_running_loop())
            store.load()
            await store.refresh_ffmpeg()
            mp4 = self.folder / "clip.mp4"
            mp4.write_bytes(b"x")
            self.media = self.media.__class__(**{**self.media.__dict__, "path": mp4})
            await store.load_source(str(mp4))
            store.set_output(str(mp4))
            await store.start()
            self.assertEqual(self.notices[-1][0], "error")
            self.assertFalse(store.state.running)

        asyncio.run(run())

    def test_cover_grabs_and_selection(self):
        from app.core.tools import covers

        def fake_extract(_ffmpeg, _path, candidates, on_frame, *, should_stop):
            for candidate in candidates:
                on_frame(candidate, None if candidate.index == 3 else b"png", "boom" if candidate.index == 3 else "")

        async def run():
            store = self._store(asyncio.get_running_loop())
            store.load()
            await store.refresh_ffmpeg()
            with mock.patch.object(covers, "extract_covers", fake_extract):
                await store.load_cover_source(str(self.video))
                await asyncio.sleep(0)
            self.assertEqual(len(store.frames), 15)
            self.assertEqual(store.frame_errors, {3: "boom"})
            self.assertEqual(store.state.selected, 0)
            store.move(4)
            self.assertEqual(store.state.selected, 4)
            store.set_crop(False)
            self.assertEqual(self.ui["tools_cover_crop_v1"], "0")
            # A newer file drops the old grabs.
            await store.load_cover_source("")
            self.assertEqual(store.frames, {})

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
