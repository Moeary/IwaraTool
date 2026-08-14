import unittest
from unittest.mock import patch

from app.core.oreno3d import Oreno3DClient
from app.core.oreno3d_search import (
    map_oreno3d_sort,
    parse_oreno3d_query,
)
from app.core.search import SearchFilters
from app.ui.search_page import SearchWorker


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
            "tag",
        )
        self.assertEqual(
            parse_oreno3d_query("tag:genshin").entity_id,
            "genshin",
        )
        query = parse_oreno3d_query("https://oreno3d.com/characters/traveler")
        self.assertEqual((query.search_type, query.entity_id), ("character", "traveler"))

    def test_tag_scope_uses_direct_index_only_for_one_term(self):
        direct = parse_oreno3d_query("原神", scope="tags")
        self.assertEqual((direct.search_type, direct.entity_id), ("tag", "原神"))
        combined = parse_oreno3d_query("原神 mmd", scope="tags")
        self.assertEqual(combined.keyword, "原神 mmd")
        self.assertFalse(combined.is_entity_search)

    def test_sort_mapping_has_safe_default(self):
        self.assertEqual(map_oreno3d_sort("likes"), "favorites")
        self.assertEqual(map_oreno3d_sort("unknown"), "latest")


class Oreno3DEntityRequestTests(unittest.TestCase):
    def test_entity_request_uses_entity_path_without_keyword(self):
        session = _FakeSession()
        Oreno3DClient(session).fetch_entity_page(
            "tag",
            "genshin impact",
            page=2,
            sort="latest",
        )
        url, kwargs = session.calls[0]
        self.assertEqual(url, "https://oreno3d.com/tags/genshin%20impact")
        self.assertEqual(kwargs["params"], {"page": 2, "sort": "latest"})

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
            {"search_type": "tag", "entity_id": "genshin"},
        )
        self.assertEqual(len(results), 1)


if __name__ == "__main__":
    unittest.main()
