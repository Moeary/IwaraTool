"""One back history for the window: restore exact places, Esc / mouse back, close and language rebuild."""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QPointF, QSettings, Qt, QThread
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from app.config import app_config
from app.core import net_policy
from app.core.search import SearchVideo


def _videos(count: int, prefix: str = "v") -> list[SearchVideo]:
    return [SearchVideo(video_id=f"{prefix}{i}", title=f"title {prefix}{i}", author_username="bob") for i in range(count)]


def _flush():
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QApplication.processEvents()


def _spin(condition, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return condition()


class _SlowRequestWorker(QThread):
    """Stands in for a page worker stuck in a slow request's retry back-off."""

    def __init__(self):
        super().__init__()
        self.cancelled = False

    def run(self):
        try:
            net_policy.cancellable_sleep(20)
        except net_policy.RequestCancelled:
            self.cancelled = True


class _WindowCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        from app.ui.main_window import MainWindow

        self.MainWindow = MainWindow
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        settings = QSettings(os.path.join(self.tmp.name, "config.ini"), QSettings.Format.IniFormat)
        for target, value in (
            ("app.config.app_config._qs", settings),
            ("app.ui.home_workers.FeedWorker.run", lambda worker: None),
            ("app.ui.home_workers.ApiCallWorker.run", lambda worker: None),
            ("app.ui.media_detail.DetailWorker.run", lambda worker: None),
            ("app.ui.search_workers.SearchImageWorker.run", lambda worker: None),
            ("app.ui.subscription_views.CoverLoader.request", lambda *args, **kwargs: None),
            ("app.ui.subscription_page.SubscriptionInterface._start_avatar_worker_for_missing_sources", lambda page: None),
        ):
            patcher = mock.patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        previous = MainWindow._window_ref
        self.addCleanup(lambda: setattr(MainWindow, "_window_ref", previous))
        self.window = self._new_window()

    def _new_window(self):
        window = self.MainWindow()
        window.resize(1300, 900)
        window.show()
        QApplication.processEvents()
        self.addCleanup(self._discard, window)
        return window

    def _discard(self, window):
        try:
            window._reloading_language = True
            window.close()
            window.deleteLater()
        except RuntimeError:
            pass
        _flush()

    def _current(self):
        return self.window.stackedWidget.currentWidget()


class SearchRoundTripTests(_WindowCase):
    def _search_on_page_three(self):
        from app.signal_bus import signal_bus

        self.window.switchTo(self.window._search_page)
        page = self.window._search_page
        page._set_combo_data(page._source_combo, "iwara")
        page._keyword_edit.setText("miku")
        page._all_videos = _videos(60)
        page._current_page, page._last_page = 2, 5
        page._render_results()
        page._set_loading(False)
        page._item_by_key["video:v7"].setSelected(True)
        QApplication.processEvents()
        bar = page._results.verticalScrollBar()
        bar.setValue(bar.maximum() // 2)
        return page, signal_bus

    def test_search_page_three_to_author_to_detail_and_back_twice(self):
        page, signal_bus = self._search_on_page_three()
        scroll = page._results.verticalScrollBar().value()
        self.assertGreater(scroll, 0)

        signal_bus.author_page_requested.emit(("bob", "Bob", "u2", ""))  # an author not subscribed here
        subs = self.window._subscription_page
        self.assertIs(self._current(), subs)
        self.assertIs(subs._view_stack.currentWidget(), subs._author_view)
        signal_bus.media_detail_requested.emit("video", "x1")
        self.assertIs(self._current(), self.window._home_page)

        self.assertTrue(self.window.navigate_back())
        self.assertIs(self._current(), subs)
        self.assertEqual(subs._author_view.username, "bob")

        page._all_videos = []  # restoring must not depend on what the page holds now
        self.assertTrue(self.window.navigate_back())
        self.assertIs(self._current(), page)
        _spin(lambda: page._results.verticalScrollBar().value() == scroll, 1.0)
        self.assertEqual(page._keyword_edit.text(), "miku")
        self.assertEqual(page._current_page, 2)
        self.assertEqual(len(page._all_videos), 60)
        self.assertEqual(page._selected_keys(), ("video:v7",))
        self.assertEqual(page._results.verticalScrollBar().value(), scroll)
        self.assertTrue(self.window.navigate_back())  # then the page the app opened on
        self.assertIs(self._current(), self.window._home_page)
        self.assertFalse(self.window.navigate_back())  # and nothing before that: stay
        self.assertIs(self._current(), self.window._home_page)

    def test_a_tag_search_from_elsewhere_can_be_undone(self):
        page, signal_bus = self._search_on_page_three()
        with mock.patch.object(page, "_start_search"):
            signal_bus.search_requested.emit({"scope": "tags", "keyword": "dance"})
        self.assertEqual(page._keyword_edit.text(), "dance")
        self.assertTrue(self.window.navigate_back())
        self.assertEqual(page._keyword_edit.text(), "miku")
        self.assertEqual(str(page._scope_combo.currentData()), "videos")
        self.assertEqual(len(page._all_videos), 60)

    def test_viewing_an_authors_works_inside_search_can_be_undone(self):
        page, _signal_bus = self._search_on_page_three()
        with mock.patch.object(page, "_start_search"):
            page._show_author_works_target(("bob", "Bob", "u2", ""))
        self.assertIsNotNone(page._author_video_target)
        self.assertTrue(self.window.navigate_back())
        self.assertIsNone(page._author_video_target)
        self.assertEqual(page._keyword_edit.text(), "miku")
        self.assertEqual(page._current_page, 2)

    def test_sidebar_switches_are_steps_too(self):
        self.window.switchTo(self.window._search_page)
        self.window.switchTo(self.window._history_page)
        self.assertTrue(self.window.navigate_back())
        self.assertIs(self._current(), self.window._search_page)


class BackInputTests(_WindowCase):
    def test_the_mouse_back_button_goes_back_from_any_widget(self):
        self.window.switchTo(self.window._search_page)
        self.window.switchTo(self.window._history_page)
        target = self.window._history_page
        for kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
            event = QMouseEvent(
                kind, QPointF(5, 5), QPointF(5, 5), Qt.MouseButton.BackButton,
                Qt.MouseButton.BackButton if kind == QEvent.Type.MouseButtonPress else Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
            )
            QApplication.sendEvent(target, event)
        self.assertIs(self._current(), self.window._search_page)  # one step for press + release

    def test_escape_clears_a_selection_first_then_goes_back(self):
        page = self.window._search_page
        self.window.switchTo(page)
        page._all_videos = _videos(3)
        page._render_results()
        self.window.switchTo(self.window._subscription_page)
        self.window.switchTo(page)
        page._item_by_key["video:v1"].setSelected(True)
        self.window.escape_back()
        self.assertIs(self._current(), page)
        self.assertEqual(page._selected_keys(), ())
        self.window.escape_back()
        self.assertIs(self._current(), self.window._subscription_page)

    def test_escape_with_no_history_does_nothing(self):
        self.window.navigation.clear()
        current = self._current()
        self.window.escape_back()
        self.assertIs(self._current(), current)

    def test_escape_is_the_one_back_key(self):
        from app.core import shortcut_defs

        esc = [a.id for a in shortcut_defs.all_actions() if shortcut_defs.key_for(a.id) == "Esc" and a.scope != "player"]
        self.assertEqual(esc, ["nav_back"])


class CloseAndLanguageTests(_WindowCase):
    def _main_windows(self):
        alive = []
        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, self.MainWindow) and not widget._close_complete:
                alive.append(widget)
        return alive

    def test_closing_with_a_slow_request_does_not_block_the_interface(self):
        page = self.window._search_page
        worker = _SlowRequestWorker()
        page._search_workers.append(worker)
        worker.start()
        self.addCleanup(worker.wait, 5000)
        self.window._reloading_language = True  # a replaced window: closing must not end the test app
        started = time.monotonic()
        self.window.close()
        self.assertLess(time.monotonic() - started, 0.5)  # returned at once, nothing waited on
        self.assertFalse(self.window.isVisible())
        self.assertTrue(self.window._closing)
        self.assertTrue(_spin(lambda: not worker.isRunning(), 3.0))
        self.assertTrue(worker.cancelled)  # the back-off wait ended on the interruption
        page._search_workers.remove(worker)
        self.assertTrue(_spin(lambda: self.window._close_complete, 3.0))

    def test_switching_language_twice_leaves_one_window_and_one_set_of_shortcuts(self):
        from app.ui.shortcuts import registry

        page = self.window._search_page
        self.window.switchTo(page)
        page._keyword_edit.setText("miku")
        page._all_videos = _videos(5)
        page._render_results()
        before = len(self._main_windows())
        self.window._on_language_changed("ja")
        second = self.MainWindow._window_ref
        self.addCleanup(self._discard, second)
        second._on_language_changed("en")
        third = self.MainWindow._window_ref
        self.addCleanup(self._discard, third)
        _flush()
        _flush()
        alive = self._main_windows()
        self.assertEqual(len(alive), before - 1 + 1)  # the original was replaced, not kept
        self.assertEqual(alive.count(third), 1)
        self.assertEqual(len(registry.live("home_refresh")), 1)
        self.assertEqual(len(registry.live("nav_back")), 1)
        self.assertIs(third.stackedWidget.currentWidget(), third._search_page)
        self.assertEqual(third._search_page._keyword_edit.text(), "miku")
        self.assertEqual(len(third._search_page._all_videos), 5)
        tray_icons = [w._tray_icon for w in alive if w._tray_icon is not None]
        self.assertLessEqual(len([icon for icon in tray_icons if icon.isVisible()]), 1)

    def test_language_rebuild_keeps_the_back_history(self):
        self.window.switchTo(self.window._search_page)
        self.window.switchTo(self.window._history_page)
        self.window._on_language_changed("ja")
        new = self.MainWindow._window_ref
        self.addCleanup(self._discard, new)
        _flush()
        self.assertIs(new.stackedWidget.currentWidget(), new._history_page)
        self.assertTrue(new.navigate_back())
        self.assertIs(new.stackedWidget.currentWidget(), new._search_page)


if __name__ == "__main__":
    unittest.main()
