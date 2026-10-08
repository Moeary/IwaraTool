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
        self.window._audio.setVolume(0.5)
        self.player = self.window._player
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
        self.window.toggle_play()
        self.assertEqual(self.player.playbackState(), QMediaPlayer.PlaybackState.PausedState)
        self.window.toggle_play()
        self.assertEqual(self.player.playbackState(), QMediaPlayer.PlaybackState.PlayingState)

    def test_seek_is_clamped_to_the_media(self):
        self.player._duration = 60_000
        self.player._position = 58_000
        self.window.seek_by(5_000)
        self.assertEqual(self.player.position(), 60_000)
        self.window.seek_by(-100_000)
        self.assertEqual(self.player.position(), 0)

    def test_speed_combo_and_steps(self):
        self.window.set_speed(1.5)
        self.assertEqual(self.player.playbackRate(), 1.5)
        self.window.step_speed(+1)
        self.assertEqual(self.player.playbackRate(), 2.0)
        self.window.step_speed(-1)
        self.window.step_speed(-1)
        self.assertEqual(self.player.playbackRate(), 1.25)

    def test_new_video_resets_speed(self):
        self.window.set_speed(3.0)
        self.window.play_local("a.mp4")
        self.assertEqual(self.player.playbackRate(), 1.0)

    def test_hold_boosts_and_release_restores(self):
        self.window.set_speed(1.0)
        self.window._hold_timer.setInterval(5)
        QTest.mousePress(self.window._video, Qt.MouseButton.LeftButton)
        for _ in range(50):
            QTest.qWait(20)
            if self.window._boosted:
                break
        self.assertEqual(self.player.playbackRate(), vpw.HOLD_BOOST_RATE)
        self.assertTrue(self.window._boosted)
        QTest.mouseRelease(self.window._video, Qt.MouseButton.LeftButton)
        self.assertEqual(self.player.playbackRate(), 1.0)
        self.assertFalse(self.window._boosted)

    def test_boost_never_slows_down_a_faster_rate(self):
        self.window.set_speed(3.0)
        self.window._begin_boost()
        self.assertEqual(self.player.playbackRate(), 3.0)
        self.window._end_boost()
        self.assertEqual(self.player.playbackRate(), 3.0)

    def test_short_tap_toggles_play_after_the_double_click_window(self):
        self.window.play_local("a.mp4")
        self.window._click_timer.setInterval(5)
        with mock.patch.object(QApplication, "doubleClickInterval", return_value=5):
            QTest.mouseClick(self.window._video, Qt.MouseButton.LeftButton)
            QTest.qWait(60)
        self.assertEqual(self.player.playbackState(), QMediaPlayer.PlaybackState.PausedState)
        self.assertFalse(self.window._boosted)

    def test_keyboard_shortcuts(self):
        self.window.play_local("a.mp4")
        self.player._duration = 100_000
        self.player._position = 10_000
        QTest.keyClick(self.window, Qt.Key.Key_Left)
        self.assertEqual(self.player.position(), 5_000)
        QTest.keyClick(self.window, Qt.Key.Key_Right)
        self.assertEqual(self.player.position(), 10_000)
        QTest.keyClick(self.window, Qt.Key.Key_Space)
        self.assertEqual(self.player.playbackState(), QMediaPlayer.PlaybackState.PausedState)
        before = self.window._audio.volume()
        QTest.keyClick(self.window, Qt.Key.Key_Up)
        self.assertGreater(self.window._audio.volume(), before)
        QTest.keyClick(self.window, Qt.Key.Key_M)
        self.assertTrue(self.window._audio.isMuted())

    def test_stale_stream_result_is_ignored(self):
        self.window.play_local("a.mp4")  # bumps the request id
        stale = preview_stream.PreviewStream("http://127.0.0.1:1/s/x", "T", "540")
        self.window._on_stream_ready(stale, "", self.window._request_id - 1)
        self.assertTrue(self.player.source.isLocalFile())

    def test_stream_error_is_shown(self):
        self.window.play_local("a.mp4")
        self.window._on_stream_ready(None, "private video", self.window._request_id)
        self.assertIn("private video", self.window._overlay.text())
        self.assertFalse(self.window._overlay.isHidden())

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


if __name__ == "__main__":
    unittest.main()
