import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from app.core.oreno3d import Oreno3DClient, Oreno3DListing
from app.core.oreno3d_search import (
    apply_tag_suggestion,
    intersect_oreno3d_listings,
    map_oreno3d_sort,
    parse_oreno3d_query,
    parse_oreno3d_tag_ids,
    tag_suggestion_query,
)
from app.core.search import SearchFilters
from app.ui.search_page import SearchWorker, TagSuggestionPopup


class _FakeResponse:
    status_code = 200
    text = "<div class='g-main-grid'></div>"

    def close(self):
        pass


class _FakeSession:
    def __init__(self):
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _FakeResponse()


class Oreno3DSearchQueryTests(unittest.TestCase):
    def test_entity_prefix_and_url_are_normalized(self):
        self.assertEqual(
            parse_oreno3d_query("tag:genshin").search_type,
            "origin",
        )
        self.assertEqual(
            parse_oreno3d_query("tag:genshin").entity_id,
            "276",
        )
        query = parse_oreno3d_query("https://oreno3d.com/characters/traveler")
        self.assertEqual((query.search_type, query.entity_id), ("character", "traveler"))

    def test_tag_scope_uses_direct_index_only_for_one_term(self):
        direct = parse_oreno3d_query("原神", scope="tags")
        self.assertEqual((direct.search_type, direct.entity_id), ("origin", "276"))
        combined = parse_oreno3d_query("原神 mmd", scope="tags")
        self.assertEqual(combined.keyword, "原神 mmd")
        self.assertFalse(combined.is_entity_search)

    def test_localized_entity_name_is_normalized_to_numeric_route(self):
        query = parse_oreno3d_query("tag:azur_lane", scope="tags")
        self.assertEqual((query.search_type, query.entity_id), ("origin", "14"))
        self.assertEqual(
            (parse_oreno3d_query("1234", scope="tags").search_type,
             parse_oreno3d_query("1234", scope="tags").entity_id),
            ("tag", "1234"),
        )

    def test_sort_mapping_has_safe_default(self):
        self.assertEqual(map_oreno3d_sort("likes"), "favorites")
        self.assertEqual(map_oreno3d_sort("unknown"), "latest")

    def test_multi_tag_input_is_split_into_direct_tag_ids(self):
        self.assertEqual(
            parse_oreno3d_tag_ids("tag:azur_lane, rape, "),
            ("azur_lane", "rape"),
        )

    def test_autocomplete_preserves_explicit_tag_prefix(self):
        self.assertEqual(tag_suggestion_query("tag:azur"), "azur")
        self.assertEqual(
            apply_tag_suggestion("mmd, tag:azur", "azur_lane"),
            "mmd, tag:azur_lane, ",
        )

    def test_multi_tag_listing_intersection_merges_tags(self):
        first = Oreno3DListing(
            "movie-1", "https://oreno3d.com/movies/movie-1", "one", "a", "", 1, 1, ("azur_lane",)
        )
        first_only = Oreno3DListing(
            "movie-2", "https://oreno3d.com/movies/movie-2", "two", "a", "", 1, 1, ("azur_lane",)
        )
        second = Oreno3DListing(
            "movie-1", "https://oreno3d.com/movies/movie-1", "one", "a", "", 1, 1, ("rape",)
        )
        result = intersect_oreno3d_listings(((first, first_only), (second,)))
        self.assertEqual([item.source_id for item in result], ["movie-1"])
        self.assertEqual(result[0].tags, ("azur_lane", "rape"))


class Oreno3DEntityRequestTests(unittest.TestCase):
    def test_entity_request_uses_entity_path_without_keyword(self):
        session = _FakeSession()
        Oreno3DClient(session).fetch_entity_page(
            "tag",
            "2",
            page=2,
            sort="latest",
        )
        url, kwargs = session.calls[0]
        self.assertEqual(url, "https://oreno3d.com/tags/2")
        self.assertEqual(kwargs["params"], {"page": 2, "sort": "latest"})

    def test_unknown_named_entity_falls_back_to_keyword_search(self):
        session = _FakeSession()
        Oreno3DClient(session).fetch_entity_page(
            "tag",
            "not_in_oreno_map",
            page=2,
            sort="latest",
        )
        url, kwargs = session.calls[0]
        self.assertEqual(url, "https://oreno3d.com/search")
        self.assertEqual(
            kwargs["params"],
            {"page": 2, "keyword": "not_in_oreno_map", "sort": "latest"},
        )

    def test_mapped_named_entity_uses_typed_numeric_route(self):
        session = _FakeSession()
        Oreno3DClient(session).fetch_entity_page(
            "tag",
            "azur_lane",
            page=2,
            sort="latest",
        )
        url, kwargs = session.calls[0]
        self.assertEqual(url, "https://oreno3d.com/origins/14")
        self.assertEqual(kwargs["params"], {"page": 2, "sort": "latest"})

    def test_named_multi_tag_requests_use_keyword_endpoint(self):
        session = _FakeSession()
        client = Oreno3DClient(session)
        for tag in ("azur_lane", "elf"):
            client.fetch_entity_page("tag", tag, page=1, sort="latest")

        self.assertEqual(
            [url for url, _kwargs in session.calls],
            ["https://oreno3d.com/origins/14", "https://oreno3d.com/search"],
        )
        self.assertEqual(
            [kwargs["params"] for _url, kwargs in session.calls],
            [
                {"page": 1, "sort": "latest"},
                {"page": 1, "keyword": "elf", "sort": "latest"},
            ],
        )

    def test_free_text_request_keeps_keyword_endpoint(self):
        session = _FakeSession()
        Oreno3DClient(session).fetch_search_page("原神", page=3, sort="hot")
        url, kwargs = session.calls[0]
        self.assertEqual(url, "https://oreno3d.com/search")
        self.assertEqual(
            kwargs["params"],
            {"page": 3, "keyword": "原神", "sort": "hot"},
        )


class Oreno3DSearchWorkerTests(unittest.TestCase):
    def test_worker_forwards_tag_query_as_entity_request(self):
        class _FakeManager:
            def __init__(self):
                self.call = None

            def get_oreno3d_search_page(self, keyword, **kwargs):
                self.call = (keyword, kwargs)
                return [], 1

        manager = _FakeManager()
        results = []
        with patch("app.ui.search_page.download_manager", manager):
            worker = SearchWorker(
                SearchFilters(keyword="tag:genshin", sort="date"),
                "videos",
                0,
                1,
                replace_results=True,
                source="oreno3d",
            )
            worker.result_ready.connect(results.append)
            worker.run()

        self.assertEqual(manager.call[0], "")
        self.assertEqual(
            {
                key: manager.call[1][key]
                for key in ("search_type", "entity_id")
            },
            {"search_type": "origin", "entity_id": "276"},
        )
        self.assertEqual(len(results), 1)

    def test_worker_uses_multi_tag_intersection(self):
        class _FakeManager:
            def __init__(self):
                self.call = None

            def get_oreno3d_tag_search_page(self, tags, **kwargs):
                self.call = (tuple(tags), kwargs)
                return [], 1

        manager = _FakeManager()
        with patch("app.ui.search_page.download_manager", manager):
            worker = SearchWorker(
                SearchFilters(keyword="azur_lane, rape", sort="date"),
                "tags",
                0,
                1,
                replace_results=True,
                source="oreno3d",
            )
            worker.run()

        self.assertEqual(manager.call[0], ("azur_lane", "rape"))


class TagSuggestionPopupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_popup_does_not_capture_keyboard_focus(self):
        popup = TagSuggestionPopup()
        self.assertNotEqual(popup.windowType(), Qt.WindowType.Popup)
        self.assertEqual(popup.focusPolicy(), Qt.FocusPolicy.NoFocus)
        popup.deleteLater()


if __name__ == "__main__":
    unittest.main()
