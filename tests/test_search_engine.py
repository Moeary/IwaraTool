"""Main search engine setting and Chinese keywords routed to Oreno3D."""
from __future__ import annotations

import os
import tempfile
import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QSettings
from PySide6.QtWidgets import QApplication

from app.config import app_config
from app.signal_bus import signal_bus
from app.ui.search_page import SearchInterface, is_chinese_keyword


class ChineseKeywordTests(unittest.TestCase):
    def test_han_without_kana_is_chinese(self):
        self.assertTrue(is_chinese_keyword("原神 舞蹈"))
        self.assertTrue(is_chinese_keyword("miku 跳舞"))
        self.assertFalse(is_chinese_keyword("初音ミク"))  # kana: Japanese
        self.assertFalse(is_chinese_keyword("miku dance"))
        self.assertFalse(is_chinese_keyword(""))


class _PageCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.context = ExitStack()
        self.addCleanup(self.context.close)
        root = self.context.enter_context(tempfile.TemporaryDirectory())
        settings = QSettings(os.path.join(root, "config.ini"), QSettings.Format.IniFormat)
        self.context.enter_context(patch.object(app_config, "_qs", settings))
        app_config.search_auto_search_enabled = False
        manager = Mock()
        manager.history.get_records.return_value = {}
        manager.get_search_tag_suggestions.return_value = []
        self.context.enter_context(patch("app.ui.search_page.download_manager", manager))
        self.context.enter_context(patch("app.ui.rules_page.RulePicker.refresh_rules"))

    def _page(self) -> SearchInterface:
        page = SearchInterface()
        self.addCleanup(self._close, page)
        self.run_search = self.context.enter_context(patch.object(page, "_run_search"))
        return page

    def _close(self, page):
        page.shutdown(timeout_ms=1000)
        page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


class SearchEngineTests(_PageCase):
    def _search(self, page, keyword: str):
        page._keyword_edit.setText(keyword)
        page._keyword_edit.textEdited.emit(keyword)  # as if typed
        page._start_search()
        filters, scope = self.run_search.call_args.args[:2]
        return filters.keyword, scope, self.run_search.call_args.kwargs["source"]

    def test_default_engine_is_iwara(self):
        self.assertEqual(app_config.search_default_source, "iwara")
        self.assertEqual(self._page()._source_combo.currentData(), "iwara")

    def test_page_starts_on_the_configured_engine(self):
        app_config.search_default_source = "oreno3d"
        self.assertEqual(self._page()._source_combo.currentData(), "oreno3d")

    def test_unknown_engine_falls_back_to_iwara(self):
        app_config._set("search_default_source", "bing")
        self.assertEqual(app_config.search_default_source, "iwara")

    def test_chinese_video_keyword_goes_to_oreno3d_and_back(self):
        page = self._page()
        self.assertEqual(self._search(page, "原神 舞蹈"), ("原神 舞蹈", "videos", "oreno3d"))
        self.assertEqual(page._source_combo.currentData(), "oreno3d")
        self.assertEqual(self._search(page, "miku"), ("miku", "videos", "iwara"))

    def test_japanese_keyword_stays_on_iwara(self):
        page = self._page()
        self.assertEqual(self._search(page, "初音ミク")[2], "iwara")

    def test_routing_can_be_turned_off(self):
        app_config.search_chinese_via_oreno3d = False
        page = self._page()
        self.assertEqual(self._search(page, "原神")[2], "iwara")

    def test_tag_searches_are_not_rerouted(self):
        page = self._page()
        page._set_combo_data(page._scope_combo, "tags")
        page._source_pinned = False
        self.assertEqual(self._search(page, "原神")[1:], ("tags", "iwara"))

    def test_a_source_picked_by_hand_is_kept_for_that_keyword(self):
        page = self._page()
        page._keyword_edit.setText("原神")
        page._source_combo.setCurrentIndex(0)  # Oreno3D...
        page._set_combo_data(page._source_combo, "iwara")  # ...then Iwara again, by hand
        page._keyword_edit.setText("原神")
        page._start_search()
        self.assertEqual(self.run_search.call_args.kwargs["source"], "iwara")
        self.assertEqual(self._search(page, "原神 舞蹈")[2], "oreno3d")  # a new keyword: routed again

    def test_changing_the_setting_moves_the_page_to_that_engine(self):
        page = self._page()
        page._keyword_edit.setText("miku")
        signal_bus.search_source_changed.emit("oreno3d")
        self.assertEqual(page._source_combo.currentData(), "oreno3d")
        self.assertEqual(page._keyword_edit.text(), "miku")



class InitialLoadTests(_PageCase):
    """Opening an empty Search page lists videos without a manual refresh."""

    def _show(self, page):
        page.resize(1000, 700)
        page.show()
        QApplication.processEvents()
        QApplication.processEvents()

    def test_empty_page_loads_the_listing_when_shown(self):
        page = self._page()
        self._show(page)
        self.run_search.assert_called_once()
        filters, scope = self.run_search.call_args.args[:2]
        self.assertEqual((filters.keyword, scope, self.run_search.call_args.kwargs["source"]), ("", "videos", "iwara"))
        self.assertIsNotNone(page._active_search_request)
        self.assertEqual(app_config.get_ui_value("search_history_v1", "[]"), "[]")  # not a search the user made

    def test_page_with_results_or_a_running_search_is_left_alone(self):
        page = self._page()
        page._active_search_request = object()
        self._show(page)
        self.run_search.assert_not_called()

    def test_scopes_that_need_a_keyword_do_not_load(self):
        page = self._page()
        page._set_combo_data(page._scope_combo, "authors")
        self._show(page)
        self.run_search.assert_not_called()

    def test_showing_again_does_not_search_again(self):
        page = self._page()
        self._show(page)
        page.hide()
        self._show(page)
        self.run_search.assert_called_once()


class SearchHistoryUpsertTests(unittest.TestCase):
    def test_an_entry_not_worth_keeping_leaves_the_history_alone(self):
        from app.ui.search_widgets import _upsert_search_history

        history = [{"keyword": "miku", "source": "iwara", "scope": "videos", "sort": "date"}]
        kept = _upsert_search_history(history, {"keyword": "", "source": "iwara", "scope": "images"})
        self.assertEqual(kept, history)


if __name__ == "__main__":
    unittest.main()
