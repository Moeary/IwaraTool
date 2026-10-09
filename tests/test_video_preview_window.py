from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from app.core import preview_stream
from app.ui import video_preview_window as vpw
from app.ui.video_preview_window import Backend, VideoPreviewWindow, format_time, nearest_speed_index


class FakePlayer(QObject):
    positionChanged = Signal(int)
    durationChanged = Signal(int)
    playbackStateChanged = Signal(object)
    mediaStatusChanged = Signal(object)
    errorOccurred = Signal(object, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = QMediaPlayer.PlaybackState.StoppedState
        self._position = 0
        self._duration = 0
        self._rate = 1.0
        self.source = QUrl()
        self.rate_history: list[float] = []

    def setSource(self, url):
        self.source = url

    def play(self):
        self._state = QMediaPlayer.PlaybackState.PlayingState
        self.playbackStateChanged.emit(self._state)

    def pause(self):
        self._state = QMediaPlayer.PlaybackState.PausedState
        self.playbackStateChanged.emit(self._state)

    def stop(self):
        self._state = QMediaPlayer.PlaybackState.StoppedState
        self._position = 0

    def playbackState(self):
        return self._state

    def position(self):
        return self._position

    def setPosition(self, value):
        self._position = int(value)

    def duration(self):
        return self._duration

    def playbackRate(self):
        return self._rate

    def setPlaybackRate(self, rate):
        self._rate = float(rate)
        self.rate_history.append(self._rate)

    def errorString(self):
        return ""


class FakeAudio:
    def __init__(self):
        self._volume, self._muted = 0.5, False

    def volume(self):
        return self._volume

    def setVolume(self, value):
        self._volume = float(value)

    def isMuted(self):
        return self._muted

    def setMuted(self, value):
        self._muted = bool(value)


def fake_backend(parent):
    return Backend(FakePlayer(parent), FakeAudio(), QWidget(parent))


class FormatTests(unittest.TestCase):
    def test_time_format(self):
        self.assertEqual(format_time(0), "00:00")
        self.assertEqual(format_time(65_000), "01:05")
        self.assertEqual(format_time(3_725_000), "1:02:05")
        self.assertEqual(format_time(-5), "00:00")

    def test_nearest_speed(self):
        self.assertEqual(vpw.SPEEDS[nearest_speed_index(1.0)], 1.0)
        self.assertEqual(vpw.SPEEDS[nearest_speed_index(1.9)], 2.0)
        self.assertEqual(vpw.SPEEDS[nearest_speed_index(9)], 3.0)


class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        from app.config import app_config

        saved_volume = app_config.get_ui_value("preview_volume_v1", None)
        self.addCleanup(lambda: app_config.set_ui_value("preview_volume_v1", saved_volume if saved_volume is not None else 0.8))
        self.window = VideoPreviewWindow(backend_factory=fake_backend)
        self.p = self.window.player
        self.p._audio.setVolume(0.5)
        self.player = self.p._player
        self.addCleanup(self._close)

    def _close(self):
        with mock.patch.object(preview_stream, "get_proxy") as proxy:
            proxy.return_value.unregister_all = lambda: None
            self.window.close()
        self.window.deleteLater()

    def test_local_playback_starts_and_toggles(self):
        self.window.play_local(r"C:\v\a.mp4", "Title", "abc")
        self.assertTrue(self.player.source.isLocalFile())
        self.assertEqual(self.window.windowTitle(), "Title")
        self.assertEqual(self.player.playbackState(), QMediaPlayer.PlaybackState.PlayingState)
        self.p.toggle_play()
        self.assertEqual(self.player.playbackState(), QMediaPlayer.PlaybackState.PausedState)
        self.p.toggle_play()
        self.assertEqual(self.player.playbackState(), QMediaPlayer.PlaybackState.PlayingState)

    def test_seek_is_clamped_to_the_media(self):
        self.player._duration = 60_000
        self.player._position = 58_000
        self.p.seek_by(5_000)
        self.assertEqual(self.player.position(), 60_000)
        self.p.seek_by(-100_000)
        self.assertEqual(self.player.position(), 0)

    def test_speed_combo_and_steps(self):
        self.p.set_speed(1.5)
        self.assertEqual(self.player.playbackRate(), 1.5)
        self.p.step_speed(+1)
        self.assertEqual(self.player.playbackRate(), 2.0)
        self.p.step_speed(-1)
        self.p.step_speed(-1)
        self.assertEqual(self.player.playbackRate(), 1.25)

    def test_new_video_resets_speed(self):
        self.p.set_speed(3.0)
        self.window.play_local("a.mp4")
        self.assertEqual(self.player.playbackRate(), 1.0)

    def test_hold_boosts_and_release_restores(self):
        self.p.set_speed(1.0)
        self.p._hold_timer.setInterval(5)
        QTest.mousePress(self.p._video, Qt.MouseButton.LeftButton)
        for _ in range(50):
            QTest.qWait(20)
            if self.p._boosted:
                break
        self.assertEqual(self.player.playbackRate(), vpw.HOLD_BOOST_RATE)
        self.assertTrue(self.p._boosted)
        QTest.mouseRelease(self.p._video, Qt.MouseButton.LeftButton)
        self.assertEqual(self.player.playbackRate(), 1.0)
        self.assertFalse(self.p._boosted)

    def test_boost_never_slows_down_a_faster_rate(self):
        self.p.set_speed(3.0)
        self.p._begin_boost()
        self.assertEqual(self.player.playbackRate(), 3.0)
        self.p._end_boost()
        self.assertEqual(self.player.playbackRate(), 3.0)

    def test_short_tap_toggles_play_after_the_double_click_window(self):
        self.window.play_local("a.mp4")
        self.p._click_timer.setInterval(5)
        with mock.patch.object(QApplication, "doubleClickInterval", return_value=5):
            QTest.mouseClick(self.p._video, Qt.MouseButton.LeftButton)
            QTest.qWait(60)
        self.assertEqual(self.player.playbackState(), QMediaPlayer.PlaybackState.PausedState)
        self.assertFalse(self.p._boosted)

    def test_keyboard_shortcuts(self):
        self.window.play_local("a.mp4")
        self.player._duration = 100_000
        self.player._position = 10_000
        QTest.keyClick(self.p, Qt.Key.Key_Left)
        self.assertEqual(self.player.position(), 5_000)
        QTest.keyClick(self.p, Qt.Key.Key_Right)
        self.assertEqual(self.player.position(), 10_000)
        QTest.keyClick(self.p, Qt.Key.Key_Space)
        self.assertEqual(self.player.playbackState(), QMediaPlayer.PlaybackState.PausedState)
        before = self.p._audio.volume()
        QTest.keyClick(self.p, Qt.Key.Key_Up)
        self.assertGreater(self.p._audio.volume(), before)
        QTest.keyClick(self.p, Qt.Key.Key_M)
        self.assertTrue(self.p._audio.isMuted())

    def test_stale_stream_result_is_ignored(self):
        self.window.play_local("a.mp4")  # bumps the request id
        stale = preview_stream.PreviewStream("http://127.0.0.1:1/s/x", "T", "540")
        self.p._on_stream_ready(stale, "", self.p._request_id - 1)
        self.assertTrue(self.player.source.isLocalFile())

    def test_stream_error_is_shown(self):
        self.window.play_local("a.mp4")
        self.p._on_stream_ready(None, "private video", self.p._request_id)
        self.assertIn("private video", self.p._overlay.text())
        self.assertFalse(self.p._overlay.isHidden())

    def test_remote_playback_uses_the_resolved_loopback_url(self):
        stream = preview_stream.PreviewStream("http://127.0.0.1:9/s/tok", "Remote title", "540")
        with mock.patch.object(preview_stream, "open_stream", return_value=stream):
            self.window.play_remote("abc", "")
            for _ in range(100):
                QTest.qWait(20)
                if self.player.source.toString():
                    break
        self.assertEqual(self.player.source.toString(), "http://127.0.0.1:9/s/tok")
        self.assertEqual(self.window.windowTitle(), "Remote title")



class _PlayerCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        from app.config import app_config

        saved_volume = app_config.get_ui_value("preview_volume_v1", None)
        self.addCleanup(lambda: app_config.set_ui_value("preview_volume_v1", saved_volume if saved_volume is not None else 0.8))
        proxy = mock.patch.object(preview_stream, "get_proxy")
        self.proxy = proxy.start().return_value
        self.addCleanup(proxy.stop)


class EmbeddedPlayerTests(_PlayerCase):
    """The player as a widget inside a page: fullscreen lifts it out and puts it back."""

    def setUp(self):
        super().setUp()
        self.page = QWidget()
        layout = vpw.QVBoxLayout(self.page)
        self.above = QWidget(self.page)
        layout.addWidget(self.above)
        self.player_widget = vpw.VideoPlayer(fake_backend, self.page, allow_popout=True)
        layout.addWidget(self.player_widget)
        layout.addWidget(QWidget(self.page))
        self.page.resize(800, 600)
        self.page.show()
        self.addCleanup(self._close)

    def _close(self):
        self.player_widget.shutdown()
        self.page.close()
        self.page.deleteLater()

    def test_fullscreen_lifts_the_player_out_and_back_into_its_slot(self):
        player = self.player_widget
        layout = self.page.layout()
        player.toggle_fullscreen()
        self.assertTrue(player.is_fullscreen())
        self.assertTrue(player.isWindow())
        self.assertIsNone(player.parentWidget())
        self.assertEqual(layout.indexOf(player), -1)
        QTest.keyClick(player, Qt.Key.Key_Escape)
        self.assertFalse(player.is_fullscreen())
        self.assertIs(player.parentWidget(), self.page)
        self.assertFalse(player.isWindow())
        self.assertEqual(layout.indexOf(player), 1)  # the same place in the page
        self.assertTrue(player.isVisible())

    def test_closing_the_fullscreen_window_returns_to_the_page(self):
        player = self.player_widget
        player.toggle_fullscreen()
        player.close()  # Alt+F4
        self.assertFalse(player.is_fullscreen())
        self.assertIs(player.parentWidget(), self.page)

    def test_popout_leaves_fullscreen_and_asks_for_the_window(self):
        player = self.player_widget
        asked = []
        player.popout_requested.connect(lambda: asked.append(True))
        player.play_local("a.mp4", "T", "abc")
        player.toggle_fullscreen()
        player._popout_btn.click()
        self.assertEqual(asked, [True])
        self.assertFalse(player.is_fullscreen())

    def test_closing_the_window_tells_its_owner_to_drop_it(self):
        window = VideoPreviewWindow(backend_factory=fake_backend)
        self.addCleanup(window.deleteLater)
        closed = []
        window.closed.connect(lambda: closed.append(True))
        window.show()
        window.close()
        self.assertEqual(closed, [True])

    def test_popout_button_only_where_offered(self):
        self.assertFalse(self.player_widget._popout_btn.isHidden())
        window = VideoPreviewWindow(backend_factory=fake_backend)
        self.addCleanup(window.deleteLater)
        self.assertTrue(window.player._popout_btn.isHidden())

    def test_start_position_is_applied_once_the_media_is_loaded(self):
        player = self.player_widget
        player.play_local("a.mp4", "T", "abc", start_ms=42_000)
        player._player.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.LoadedMedia)
        self.assertEqual(player._player.position(), 42_000)
        player._player.setPosition(1_000)
        player._player.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.LoadedMedia)
        self.assertEqual(player._player.position(), 1_000)  # only once

    def test_stop_releases_only_its_own_stream(self):
        player = self.player_widget
        player.play_local("a.mp4", "T", "abc")
        stream = preview_stream.PreviewStream("http://127.0.0.1:9/s/mine", "T", "540")
        player._on_stream_ready(stream, "", player._request_id)
        player.stop()
        self.proxy.unregister.assert_called_once_with("http://127.0.0.1:9/s/mine")
        self.proxy.unregister_all.assert_not_called()


class DetailInlinePlaybackTests(_PlayerCase):
    """Play on a post page plays on the page; only the window buttons open a window."""

    def setUp(self):
        super().setUp()
        from app.core.search import SearchVideo
        from app.ui import media_detail
        from app.ui.home_workers import CoverFetcher

        patcher = mock.patch.object(media_detail.DetailView, "player_backend_factory", staticmethod(fake_backend))
        patcher.start()
        self.addCleanup(patcher.stop)
        local = mock.patch("app.core.video_player.local_video_path", return_value="")
        local.start()
        self.addCleanup(local.stop)
        self.view = media_detail.DetailView(CoverFetcher())
        self.view.resize(1000, 800)
        self.view.show()
        self.addCleanup(self._close)
        self.view._kind, self.view._item_id = "video", "abc"
        self.view._video = SearchVideo(video_id="abc", title="Dance", author_username="bob")
        self.remote = mock.patch.object(vpw.VideoPlayer, "play_remote").start()
        self.addCleanup(mock.patch.stopall)

    def _close(self):
        self.view.shutdown(timeout_ms=1000)
        self.view.deleteLater()

    def _signals(self):
        from app.signal_bus import signal_bus

        windows, popouts = [], []
        signal_bus.video_preview_requested.connect(lambda *a: windows.append(a))
        signal_bus.video_popout_requested.connect(lambda *a: popouts.append(a))
        self.addCleanup(signal_bus.video_preview_requested.disconnect)
        self.addCleanup(signal_bus.video_popout_requested.disconnect)
        return windows, popouts

    def test_play_embeds_the_player_in_place_of_the_cover(self):
        windows, _popouts = self._signals()
        self.view._play()
        self.assertEqual(windows, [])  # no separate window
        self.assertTrue(self.view._player_box.isVisible())
        self.assertFalse(self.view._stage.isVisible())
        self.remote.assert_called_once_with("abc", "Dance")
        self.assertIsNotNone(self.view._player)

    def test_local_file_plays_from_disk(self):
        with mock.patch("app.core.video_player.local_video_path", return_value=r"C:\v\abc.mp4"):
            self.view._play()
        self.assertTrue(self.view._player._player.source.isLocalFile())
        self.remote.assert_not_called()

    def test_window_button_opens_the_window(self):
        windows, _popouts = self._signals()
        self.view._play_in_window()
        self.assertEqual(windows, [("abc", "Dance", "")])

    def test_popout_continues_in_the_window_at_the_same_position(self):
        _windows, popouts = self._signals()
        self.view._play()
        player = self.view._player
        player._video_id, player._title = "abc", "Dance"
        player._player.setPosition(12_345)
        player._popout_btn.click()
        self.assertEqual(popouts, [("abc", "Dance", 12_345)])
        self.assertFalse(self.view._player_box.isVisible())
        self.assertTrue(self.view._stage.isVisible())

    def test_leaving_the_post_stops_the_video(self):
        self.view._play()
        stop = mock.patch.object(self.view._player, "stop").start()
        self.view.hide()
        stop.assert_called()
        self.assertTrue(self.view._player_box.isHidden())

    def test_fullscreen_does_not_stop_the_video(self):
        self.view._play()
        stop = mock.patch.object(self.view._player, "stop").start()
        self.view._player.toggle_fullscreen()
        self.assertTrue(self.view._player.is_fullscreen())
        stop.assert_not_called()
        self.view._player.toggle_fullscreen()
        self.assertIs(self.view._player.parentWidget(), self.view._player_box)


class NormalGeometryTests(unittest.TestCase):
    """After maximize/fullscreen a window returns exactly where it was."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def test_restore_undoes_a_shift(self):
        if sys.platform != "win32":
            self.skipTest("the fix is installed on Windows only")
        window = QWidget()
        self.addCleanup(window.deleteLater)
        vpw.keep_normal_geometry(window)
        window.setGeometry(300, 200, 640, 400)
        window.show()
        QApplication.processEvents()
        window.showMaximized()
        QApplication.processEvents()
        window.showNormal()
        window.move(368, 200)  # what Qt does with the taskbar on the left
        for _ in range(5):
            QApplication.processEvents()
        self.assertEqual(window.geometry().topLeft().toTuple(), (300, 200))
        window.move(500, 250)  # a move by the user is kept
        QApplication.processEvents()
        moved = window.geometry().topLeft().toTuple()
        window.showMaximized()
        QApplication.processEvents()
        window.showNormal()
        for _ in range(5):
            QApplication.processEvents()
        self.assertEqual(window.geometry().topLeft().toTuple(), moved)


if __name__ == "__main__":
    unittest.main()
