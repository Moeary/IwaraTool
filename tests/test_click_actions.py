"""Click / double-click actions on Search, subscription and author results."""
from __future__ import annotations

import os
import tempfile
import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.config import app_config
from app.core.search import SearchVideo
from app.signal_bus import signal_bus
from app.ui import click_dispatch
from app.ui.click_dispatch import ClickDispatcher


def _videos(count: int, *, source_kind: str = "iwara") -> list[SearchVideo]:
    return [
        SearchVideo(video_id=f"v{i}", title=f"title {i}", author_username="bob", source_kind=source_kind)
        for i in range(count)
    ]


class _ConfigCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.context = ExitStack()
        self.addCleanup(self.context.close)
        root = self.context.enter_context(tempfile.TemporaryDirectory())
        settings = QSettings(os.path.join(root, "config.ini"), QSettings.Format.IniFormat)
        self.context.enter_context(patch.object(app_config, "_qs", settings))
        # No waiting out the double-click interval in tests.
        self.context.enter_context(patch.object(QApplication, "doubleClickInterval", return_value=5))

    def _listen(self, signal):
        seen = []

        def record(*args):
            seen.append(args)

        signal.connect(record)
        self.addCleanup(lambda: signal.disconnect(record))
        return seen


class ConfigTests(_ConfigCase):
    def test_defaults_select_on_click_and_open_the_detail_on_double_click(self):
        self.assertEqual(app_config.media_click_action, "select")
        self.assertEqual(app_config.media_double_click_action, "detail")

    def test_unknown_values_fall_back_to_the_defaults(self):
        app_config._set("media_click_action", "explode")
        app_config._set("media_double_click_action", "")
        self.assertEqual(app_config.media_click_action, "select")
        self.assertEqual(app_config.media_double_click_action, "detail")


class DispatcherTests(_ConfigCase):
    def _dispatcher(self):
        calls = []
        return ClickDispatcher(lambda action, target: calls.append((action, target))), calls

    def test_select_happens_at_once_and_a_double_click_undoes_it(self):
        dispatcher, calls = self._dispatcher()
        dispatcher.click("a")
        self.assertEqual(calls, [("select", "a")])
        dispatcher.double_click("a")
        self.assertEqual(calls, [("select", "a"), ("select", "a"), ("detail", "a")])

    def test_other_click_actions_wait_for_a_possible_double_click(self):
        app_config.media_click_action = "play"
        dispatcher, calls = self._dispatcher()
        dispatcher.click("a")
        self.assertEqual(calls, [])
        dispatcher.double_click("a")
        QTest.qWait(30)
        self.assertEqual(calls, [("detail", "a")])  # the click never also played it
        dispatcher.click("b")
        QTest.qWait(30)
        self.assertEqual(calls[-1], ("play", "b"))

    def test_without_a_double_click_action_a_click_is_immediate(self):
        app_config.media_click_action = "detail"
        app_config.media_double_click_action = "none"
        dispatcher, calls = self._dispatcher()
        dispatcher.click("a")
        self.assertEqual(calls, [("detail", "a")])
        dispatcher.double_click("a")
        self.assertEqual(calls, [("detail", "a")])

    def test_play_in_window_skips_image_posts(self):
        seen = self._listen(signal_bus.video_preview_requested)
        image = SearchVideo(video_id="i1", title="pic", source_kind="iwara_image")
        self.assertFalse(click_dispatch.play_in_window(image))
        self.assertTrue(click_dispatch.play_in_window(_videos(1)[0]))
        self.assertEqual(seen, [("v0", "title 0", "")])


class MediaGridClickTests(_ConfigCase):
    def _grid(self, *, configurable: bool):
        from app.ui.media_card import MediaGrid

        grid = MediaGrid(selectable=True, configurable_clicks=configurable)
        self.addCleanup(grid.deleteLater)
        grid.resize(1000, 600)
        grid.set_videos(_videos(3))
        grid.show()
        QApplication.processEvents()
        return grid

    @staticmethod
    def _card_point(card):
        return card.rect().center()

    def test_click_selects_and_double_click_opens_without_changing_the_selection(self):
        grid = self._grid(configurable=True)
        opened = self._listen(grid.card_activated)
        card = grid._cards[1]
        QTest.mouseClick(card, Qt.MouseButton.LeftButton, pos=self._card_point(card))
        self.assertTrue(card.is_selected())
        self.assertEqual(opened, [])
        other = grid._cards[2]
        QTest.mouseDClick(other, Qt.MouseButton.LeftButton, pos=self._card_point(other))
        QTest.qWait(20)
        self.assertEqual([args[0].video_id for args in opened], ["v2"])
        self.assertFalse(other.is_selected())
        self.assertTrue(card.is_selected())  # the earlier selection is kept

    def test_grids_that_do_not_opt_in_keep_click_to_open(self):
        grid = self._grid(configurable=False)
        opened = self._listen(grid.card_activated)
        card = grid._cards[0]
        QTest.mouseClick(card, Qt.MouseButton.LeftButton, pos=self._card_point(card))
        self.assertEqual([args[0].video_id for args in opened], ["v0"])
        self.assertFalse(card.is_selected())

    def test_play_action_asks_for_the_player_window(self):
        app_config.media_double_click_action = "play"
        grid = self._grid(configurable=True)
        seen = self._listen(signal_bus.video_preview_requested)
        card = grid._cards[0]
        QTest.mouseDClick(card, Qt.MouseButton.LeftButton, pos=self._card_point(card))
        self.assertEqual(seen, [("v0", "title 0", "")])


class SearchResultClickTests(_ConfigCase):
    def setUp(self):
        super().setUp()
        app_config.search_auto_search_enabled = False
        manager = Mock()
        manager.history.get_records.return_value = {}
        manager.get_search_tag_suggestions.return_value = []
        self.context.enter_context(patch("app.ui.search_page.download_manager", manager))
        self.context.enter_context(patch("app.ui.rules_page.RulePicker.refresh_rules"))
        from app.ui.search_page import SearchInterface

        self.context.enter_context(patch.object(SearchInterface, "_load_initial_results"))
        self.page = SearchInterface()
        self.addCleanup(self._close)
        self.page.resize(1200, 800)
        self.page._set_combo_data(self.page._source_combo, "iwara")

    def _close(self):
        self.page.shutdown(timeout_ms=1000)
        self.page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def _show(self, videos):
        self.page._all_videos = videos
        self.page._render_results()
        self.page.show()
        QApplication.processEvents()

    def _grid_point(self, key):
        item = self.page._item_by_key[key]
        return item, self.page._results.visualItemRect(item).center()

    def test_click_adds_to_the_selection_instead_of_replacing_it(self):
        self._show(_videos(3))
        viewport = self.page._results.viewport()
        for key in ("video:v0", "video:v2"):
            _item, point = self._grid_point(key)
            QTest.mouseClick(viewport, Qt.MouseButton.LeftButton, pos=point)
        selected = {value["key"] for value in self.page._selected_data()}
        self.assertEqual(selected, {"video:v0", "video:v2"})
        _item, point = self._grid_point("video:v0")
        QTest.mouseClick(viewport, Qt.MouseButton.LeftButton, pos=point)  # again: removed
        self.assertEqual({value["key"] for value in self.page._selected_data()}, {"video:v2"})

    def test_double_click_opens_the_detail_page_and_keeps_the_selection(self):
        self._show(_videos(2))
        opened = self._listen(signal_bus.media_detail_requested)
        previews = self._listen(signal_bus.video_preview_requested)
        _item, point = self._grid_point("video:v1")
        QTest.mouseDClick(self.page._results.viewport(), Qt.MouseButton.LeftButton, pos=point)
        QTest.qWait(20)
        self.assertEqual(opened, [("video", "v1")])
        self.assertEqual(previews, [])  # no player window any more
        self.assertEqual(self.page._selected_data(), [])

    def test_ctrl_click_keeps_qt_selection(self):
        self._show(_videos(2))
        viewport = self.page._results.viewport()
        for key in ("video:v0", "video:v1"):
            _item, point = self._grid_point(key)
            QTest.mouseClick(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier, point)
        self.assertEqual(len(self.page._selected_data()), 2)

    def test_table_rows_follow_the_same_actions(self):
        self._show(_videos(3))
        self.page._set_combo_data(self.page._view_combo, "list")
        QApplication.processEvents()
        table = self.page._results_table
        viewport = table.viewport()
        rect = table.visualItemRect(table.item(1, 1))
        QTest.mouseClick(viewport, Qt.MouseButton.LeftButton, pos=rect.center())
        self.assertEqual([r.row() for r in table.selectionModel().selectedRows()], [1])
        opened = self._listen(signal_bus.media_detail_requested)
        rect = table.visualItemRect(table.item(2, 1))
        QTest.mouseDClick(viewport, Qt.MouseButton.LeftButton, pos=rect.center())
        QTest.qWait(20)
        self.assertEqual(len(opened), 1)
        self.assertEqual([r.row() for r in table.selectionModel().selectedRows()], [1])

    def test_enter_opens_the_detail_page(self):
        self._show(_videos(2))
        opened = self._listen(signal_bus.media_detail_requested)
        item, _point = self._grid_point("video:v1")
        self.page._results.setCurrentItem(item)
        self.page._results.setFocus()
        QTest.keyClick(self.page._results, Qt.Key.Key_Return)
        self.assertEqual(opened, [("video", "v1")])

    def test_resolved_oreno3d_card_opens_the_iwara_post_not_the_card_id(self):
        video = SearchVideo(
            video_id="oreno3d:12345", title="t", source_kind="oreno3d",
            source_url="https://oreno3d.com/movies/12345", downloadable=False,
        )
        self.page._apply_oreno_link(video, {"id": "AbCdEf123", "url": "https://www.iwara.tv/video/AbCdEf123"})
        self.assertEqual(video.source_kind, "iwara")  # the card now looks like an Iwara one...
        self.assertEqual(self.page._detail_target(video), ("video", "AbCdEf123"))  # ...with the Iwara id
        opened = self._listen(signal_bus.media_detail_requested)
        self.page._open_result_detail(video)
        self.assertEqual(opened, [("video", "AbCdEf123")])

    def test_unresolved_oreno3d_card_opens_its_detail_once_resolved(self):
        video = _videos(1, source_kind="oreno3d")[0]
        with patch.object(self.page, "_start_oreno_link_resolution") as resolve:
            self.page._open_result_detail(video)
        resolve.assert_called_once()
        self.assertIn("v0", self.page._pending_detail_video_ids)
        video_with_id = SearchVideo(video_id="v0", title="t", source_kind="oreno3d", download_video_id="abc")
        opened = self._listen(signal_bus.media_detail_requested)
        self.page._open_result_detail(video_with_id)
        self.assertEqual(opened, [("video", "abc")])


if __name__ == "__main__":
    unittest.main()
