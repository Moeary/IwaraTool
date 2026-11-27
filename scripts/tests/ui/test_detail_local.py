"""A downloaded copy plays even when the post itself cannot load (stores/detail.py)."""
from __future__ import annotations

import asyncio
import importlib.util
import unittest
from types import SimpleNamespace
from unittest import mock

HAS_FLET = importlib.util.find_spec("flet") is not None


@unittest.skipUnless(HAS_FLET, "Flet is not installed in this environment")
class LocalCopyTests(unittest.TestCase):
    def test_local_copy_makes_a_stub_playable(self):
        from app.core.search.models import SearchVideo
        from app.ui.stores import detail

        store = SimpleNamespace(manager=SimpleNamespace(history=object()), closed=False)
        model = detail.DetailModel(store, "video", "abc")
        model.state.video = SearchVideo(video_id="abc", title="t", downloadable=False)  # a stored record without file info
        self.assertFalse(model.can_play)
        with mock.patch.object(detail.video_player, "downloaded_copy", return_value="/x/abc.mp4"):
            asyncio.run(model.check_local())
        self.assertEqual(model.state.local, "/x/abc.mp4")
        self.assertTrue(model.can_play)

    def test_images_never_look_for_a_copy(self):
        from app.ui.stores import detail

        model = detail.DetailModel(SimpleNamespace(manager=None, closed=False), "image", "i")
        with mock.patch.object(detail.video_player, "downloaded_copy") as look:
            asyncio.run(model.check_local())
        look.assert_not_called()


if __name__ == "__main__":
    unittest.main()
