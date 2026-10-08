"""SFW / NSFW filtering on the Search page and its worker."""
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
from app.core.api import IwaraAPI
from app.core.search import SearchFilters
from app.core.search_manager import SearchManagerMixin
from app.signal_bus import signal_bus
from app.ui.search_page import SearchInterface, SearchWorker


def _rows(*ratings: str) -> list[dict]:
    return [
        {"id": f"item{i}", "title": f"item {i}", "rating": value, "numImages": 1, "thumbnail": {"id": f"f{i}"}}
        for i, value in enumerate(ratings)
    ]


class SearchRatingRouteTests(unittest.TestCase):
    def setUp(self):
        self.context = ExitStack()
        self.addCleanup(self.context.close)
        self.api = object.__new__(IwaraAPI)
        self.api._get_json = Mock(return_value={"results": [], "count": 0})
        self.manager = SearchManagerMixin()
        self.manager._api_call = lambda method, *args, **kwargs: getattr(self.api, method)(*args, **kwargs)
        self.context.enter_context(patch("app.ui.search_page.download_manager", self.manager))

    def search(self, keyword, *, scope="videos", **options):
        results = []
        worker = SearchWorker(
            SearchFilters(keyword=keyword, **options), scope, 0, 1, replace_results=True, source="iwara",
        )
        worker.result_ready.connect(results.append)
        worker.run()
        self.assertEqual(len(results), 1)
        return results[0]

    def test_keyword_search_filters_locally_because_the_server_ignores_rating(self):
        self.api._get_json.return_value = {"results": _rows("ecchi", "general", "ecchi"), "count": 3}
        result = self.search("miku", rating="general")
        params = self.api._get_json.call_args.kwargs["params"]
        self.assertTrue(self.api._get_json.call_args.args[0].endswith("/search"))
        self.assertNotIn("rating", params)
        self.assertEqual([v.video_id for v in result.videos], ["item1"])
        result = self.search("miku", rating="ecchi")
        self.assertEqual([v.video_id for v in result.videos], ["item0", "item2"])
        result = self.search("miku")
        self.assertEqual(len(result.videos), 3)

    def test_sparse_pages_keep_scanning_until_enough_rows_match(self):
        def page_rows(page, general):
            rows = []
            for i in range(32):
                rows.append({
                    "id": f"p{page}-{i}", "title": "t",
                    "rating": "general" if i < general else "ecchi",
                })
            return {"results": rows, "count": 500}

        self.api._get_json.side_effect = [page_rows(0, 10), page_rows(1, 10), page_rows(2, 10), page_rows(3, 10)]
        result = self.search("miku", rating="general")
        self.assertEqual(self.api._get_json.call_count, 3)  # 10 + 10 + 10 reaches the target of 24
        self.assertEqual(len(result.videos), 30)
        pages = [call.kwargs["params"]["page"] for call in self.api._get_json.call_args_list]
        self.assertEqual(pages, ["0", "1", "2"])
        self.assertEqual(result.current_page, 0)
        self.assertEqual(result.next_page, 3)  # continues after the last page read
        self.assertIsNone(result.last_page)

    def test_scanning_is_capped_and_ends_when_the_results_run_out(self):
        rows = [{"id": f"x{i}", "title": "t", "rating": "ecchi"} for i in range(32)]
        self.api._get_json.side_effect = None
        self.api._get_json.return_value = {"results": rows, "count": 5000}
        result = self.search("miku", rating="general")
        self.assertEqual(self.api._get_json.call_count, 4)  # RATING_SCAN_MAX_PAGES
        self.assertEqual(result.videos, [])
        self.assertEqual(result.next_page, 4)

        self.api._get_json.reset_mock()
        self.api._get_json.return_value = {"results": rows[:5], "count": 5}
        result = self.search("miku", rating="general")
        self.assertEqual(self.api._get_json.call_count, 1)
        self.assertIsNone(result.next_page)
        self.assertEqual(result.last_page, 0)

    def test_blank_video_browse_sends_the_rating_to_the_server(self):
        self.api._get_json.return_value = {"results": _rows("general"), "count": 1}
        self.search("", sort="trending", rating="general")
        self.assertTrue(self.api._get_json.call_args.args[0].endswith("/videos"))
        params = self.api._get_json.call_args.kwargs["params"]
        self.assertEqual((params["sort"], params["rating"]), ("trending", "general"))

    def test_images_without_a_keyword_browse_the_images_endpoint(self):
        self.api._get_json.return_value = {"results": _rows("general", "ecchi"), "count": 2}
        result = self.search("", scope="images", sort="popularity", rating="ecchi")
        self.assertTrue(self.api._get_json.call_args.args[0].endswith("/images"))
        params = self.api._get_json.call_args.kwargs["params"]
        self.assertEqual((params["sort"], params["rating"]), ("popularity", "ecchi"))
        self.assertFalse(result.error)
        self.assertEqual([v.video_id for v in result.videos], ["item1"])
        self.assertTrue(all(not v.downloadable for v in result.videos))

    def test_image_keyword_search_is_filtered_locally(self):
        self.api._get_json.return_value = {"results": _rows("general", "ecchi"), "count": 2}
        result = self.search("miku", scope="images", rating="general")
        self.assertTrue(self.api._get_json.call_args.args[0].endswith("/search"))
        self.assertEqual([v.video_id for v in result.videos], ["item0"])


class SearchRatingInterfaceTests(unittest.TestCase):
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
        self.page = SearchInterface()
        self.addCleanup(self.close_page)
        self.run_search = self.context.enter_context(patch.object(self.page, "_run_search"))
        self.page._set_combo_data(self.page._source_combo, "iwara")

    def close_page(self):
        self.page.shutdown(timeout_ms=1000)
        self.page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_selector_defaults_to_all_and_lists_the_three_choices(self):
        page = self.page
        values = [page._rating_combo.itemData(i) for i in range(page._rating_combo.count())]
        self.assertEqual(values, ["all", "general", "ecchi"])
        self.assertEqual(page._selected_rating(), "all")
        self.assertEqual(page._build_filters().rating, "")

    def test_choice_reaches_the_filters_persists_and_is_broadcast(self):
        page = self.page
        seen = []

        def record(value):
            seen.append(value)

        signal_bus.content_rating_changed.connect(record)
        self.addCleanup(lambda: signal_bus.content_rating_changed.disconnect(record))
        page._set_combo_data(page._rating_combo, "general")
        self.assertEqual(seen, ["general"])
        self.assertEqual(app_config.get_ui_value("content_rating_v1"), "general")
        self.assertEqual(page._build_filters().rating, "general")

    def test_search_page_follows_a_change_from_home_without_echoing(self):
        page = self.page
        seen = []

        def record(value):
            seen.append(value)

        signal_bus.content_rating_changed.connect(record)
        self.addCleanup(lambda: signal_bus.content_rating_changed.disconnect(record))
        signal_bus.content_rating_changed.emit("ecchi")
        self.assertEqual(page._selected_rating(), "ecchi")
        self.assertEqual(seen, ["ecchi"])  # only the original emit, no echo

    def test_keyword_searches_use_big_pages_so_filtering_leaves_enough_rows(self):
        page = self.page
        page._set_combo_data(page._rating_combo, "general")
        page._keyword_edit.setText("miku")
        self.assertEqual(page._build_filters().page_size, 100)
        page._keyword_edit.setText("")
        self.assertEqual(page._build_filters().page_size, 32)

    def test_oreno3d_has_no_rating_selector_and_no_rating_filter(self):
        page = self.page
        page._set_combo_data(page._rating_combo, "ecchi")
        page._set_combo_data(page._source_combo, "oreno3d")
        self.assertTrue(page._rating_group.isHidden())
        self.assertEqual(page._build_filters().rating, "")
        page._set_combo_data(page._source_combo, "iwara")
        self.assertFalse(page._rating_group.isHidden())
        self.assertEqual(page._build_filters().rating, "ecchi")

    def test_external_query_opens_a_tag_search(self):
        page = self.page
        page.apply_external_query({"scope": "tags", "keyword": "mikumikudance", "sort": "popularity"})
        self.assertEqual(page._scope_combo.currentData(), "tags")
        self.assertEqual(page._keyword_edit.text(), "mikumikudance")
        self.assertEqual(page._sort_combo.currentData(), "popularity")
        self.run_search.assert_called()

    def test_external_image_browse_needs_no_keyword(self):
        page = self.page
        page.apply_external_query({"scope": "images", "sort": "trending"})
        self.assertEqual(page._scope_combo.currentData(), "images")
        self.assertEqual(page._keyword_edit.text(), "")
        self.assertEqual(page._sort_combo.currentData(), "trending")
        filters, scope, _source = page._active_search_request
        self.assertEqual(scope, "images")
        self.assertEqual(filters.sort, "trending")


if __name__ == "__main__":
    unittest.main()
