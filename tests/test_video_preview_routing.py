from __future__ import annotations

import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.core import video_player
from app.core.search import SearchVideo
from app.ui import main_window as main_window_module
from app.ui import search_actions
from app.ui.main_window import MainWindow
from app.ui.search_actions import SearchActionsMixin


def _video(**kwargs) -> SearchVideo:
    defaults = dict(video_id="abc", title="T", source_kind="iwara")
    defaults.update(kwargs)
    return SearchVideo(**defaults)


class SearchPreviewTests(unittest.TestCase):
    def _page(self):
        page = SimpleNamespace(
            _pending_preview_video_ids=set(),
            _start_oreno_link_resolution=mock.Mock(),
            _open_video=mock.Mock(),
            _status_label=mock.Mock(),
        )
        page._preview_video_id = SearchActionsMixin._preview_video_id
        return page

    def test_iwara_result_requests_a_preview(self):
        page = self._page()
        bus = mock.Mock()
        with mock.patch.object(search_actions, "signal_bus", bus):
            SearchActionsMixin._preview_video(page, _video())
        bus.video_preview_requested.emit.assert_called_once_with("abc", "T", "")

    def test_resolved_oreno3d_result_uses_its_iwara_id(self):
        page = self._page()
        bus = mock.Mock()
        video = _video(video_id="oreno-1", source_kind="oreno3d", iwara_url="https://www.iwara.tv/video/XyZ123abc")
        with mock.patch.object(search_actions, "signal_bus", bus):
            SearchActionsMixin._preview_video(page, video)
        self.assertEqual(bus.video_preview_requested.emit.call_args.args[0], "XyZ123abc")

    def test_unresolved_oreno3d_result_is_resolved_first(self):
        page = self._page()
        bus = mock.Mock()
        video = _video(video_id="oreno-1", source_kind="oreno3d")
        with mock.patch.object(search_actions, "signal_bus", bus):
            SearchActionsMixin._preview_video(page, video)
        bus.video_preview_requested.emit.assert_not_called()
        self.assertEqual(page._pending_preview_video_ids, {"oreno-1"})
        page._start_oreno_link_resolution.assert_called_once()

    def test_image_posts_fall_back_to_the_browser(self):
        page = self._page()
        bus = mock.Mock()
        video = _video(downloadable=False)
        with mock.patch.object(search_actions, "signal_bus", bus):
            SearchActionsMixin._preview_video(page, video)
        bus.video_preview_requested.emit.assert_not_called()
        page._open_video.assert_called_once_with(video)


class MainWindowRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def _stub(self):
        stub = SimpleNamespace(_preview_window=None, _on_preview_window_closed=lambda: None)
        stub._show_preview_window = lambda *args, **kwargs: MainWindow._show_preview_window(stub, *args, **kwargs)
        return stub

    def _route(self, *, local: str, mode: str, window):
        stub = self._stub()
        with mock.patch.object(video_player, "local_video_path", return_value=local), \
                mock.patch.object(video_player, "player_mode", return_value=mode), \
                mock.patch.object(main_window_module, "VideoPreviewWindow", return_value=window):
            MainWindow._on_video_preview_requested(stub, "abc", "Title", "")
        return stub

    def test_popout_uses_the_window_even_with_an_external_player_and_keeps_the_position(self):
        window = mock.Mock()
        stub = self._stub()
        with mock.patch.object(video_player, "local_video_path", return_value="/v/a.mp4"),                 mock.patch.object(video_player, "player_mode", return_value=video_player.MODE_SYSTEM),                 mock.patch.object(main_window_module, "VideoPreviewWindow", return_value=window):
            MainWindow._on_video_popout_requested(stub, "abc", "Title", 12_000)
        window.play_local.assert_called_once_with("/v/a.mp4", "Title", "abc", start_ms=12_000)
        window.show.assert_called_once()

    def test_downloaded_video_uses_the_external_player(self):
        window = mock.Mock()
        with mock.patch.object(video_player, "open_local_video", return_value=(True, "")) as opener:
            self._route(local="/v/a.mp4", mode=video_player.MODE_SYSTEM, window=window)
        opener.assert_called_once_with("/v/a.mp4")
        window.play_local.assert_not_called()
        window.play_remote.assert_not_called()

    def test_builtin_mode_plays_local_files_in_the_window(self):
        window = mock.Mock()
        stub = self._route(local="/v/a.mp4", mode=video_player.MODE_BUILTIN, window=window)
        window.play_local.assert_called_once_with("/v/a.mp4", "Title", "abc", start_ms=0)
        window.show.assert_called_once()
        self.assertIs(stub._preview_window, window)

    def test_missing_or_moved_video_streams_in_the_window(self):
        window = mock.Mock()
        self._route(local="", mode=video_player.MODE_SYSTEM, window=window)
        window.play_remote.assert_called_once_with("abc", "Title", start_ms=0)
        window.play_local.assert_not_called()

    def test_window_is_reused(self):
        first = mock.Mock()
        stub = self._route(local="", mode=video_player.MODE_SYSTEM, window=first)
        with mock.patch.object(video_player, "local_video_path", return_value=""), \
                mock.patch.object(main_window_module, "VideoPreviewWindow") as factory:
            MainWindow._on_video_preview_requested(stub, "def", "Other", "")
        factory.assert_not_called()
        first.play_remote.assert_called_with("def", "Other", start_ms=0)

    def test_explicit_local_path_wins_over_history(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "a.mp4")
            open(path, "wb").close()
            window = mock.Mock()
            stub = self._stub()
            with mock.patch.object(video_player, "local_video_path", return_value=""), \
                    mock.patch.object(video_player, "player_mode", return_value=video_player.MODE_BUILTIN), \
                    mock.patch.object(main_window_module, "VideoPreviewWindow", return_value=window):
                MainWindow._on_video_preview_requested(stub, "abc", "T", path)
            window.play_local.assert_called_once_with(path, "T", "abc", start_ms=0)


if __name__ == "__main__":
    unittest.main()
