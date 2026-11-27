"""Search request rules, result merging, download states and cover caching without Qt."""

from __future__ import annotations

import os
import tempfile
import unittest

from app.core.search.covers import cache_search_images
from app.core.search.models import SearchPageResult, SearchVideo
from app.presentation.search.download_status import SearchDownloadStatus, iwara_history_id, query_download_statuses
from app.presentation.search.request import PROBLEM_ERROR, PROBLEM_WARNING, ResultSet, page_size, request_problem


class RequestTests(unittest.TestCase):
    def test_problems(self):
        self.assertEqual(request_problem("images", "oreno3d", "x")[0], PROBLEM_WARNING)
        self.assertEqual(request_problem("authors", "iwara", "")[0], PROBLEM_ERROR)
        self.assertIsNone(request_problem("videos", "iwara", ""))
        self.assertIsNone(request_problem("tags", "iwara", "a"))

    def test_page_size(self):
        self.assertEqual(page_size(True, "general", "kw"), 100)
        self.assertEqual((page_size(True, "", ""), page_size(False, "", "")), (32, 36))

    def test_pages_add_up_without_duplicates(self):
        results = ResultSet()
        first = SearchPageResult(scope="videos", videos=[SearchVideo("a", "A"), SearchVideo("b", "B")], next_page=1, total=3)
        second = SearchPageResult(scope="videos", videos=[SearchVideo("b", "B"), SearchVideo("c", "C")], current_page=1)
        results.add(first, replace=True)
        results.add(second, replace=False)
        self.assertEqual([v.video_id for v in results.videos], ["a", "b", "c"])
        self.assertEqual((results.current_page, results.next_page, results.total), (1, None, None))
        results.add(first, replace=True)
        self.assertEqual(len(results.videos), 2)


class _History:
    def __init__(self, records):
        self.records = records

    def get_records(self, ids):
        return {i: self.records[i] for i in ids if i in self.records}


class DownloadStatusTests(unittest.TestCase):
    def test_states(self):
        with tempfile.NamedTemporaryFile(delete=False) as handle:
            path = handle.name
        self.addCleanup(os.unlink, path)
        history = _History({"here": {"file_path": path}, "gone": {"file_path": path + ".x"}})
        statuses = query_download_statuses(history, ["here", "gone", "new", ""])
        self.assertEqual(statuses, {"here": SearchDownloadStatus.DOWNLOADED, "gone": SearchDownloadStatus.MOVED,
                                    "new": SearchDownloadStatus.NOT_DOWNLOADED})
        self.assertEqual(iwara_history_id(SearchVideo("v1", "T")), "v1")
        self.assertEqual(iwara_history_id(SearchVideo("oreno3d:9", "T", source_kind="oreno3d")), "")


class CoverTests(unittest.TestCase):
    def test_each_cached_image_is_reported_and_clients_closed(self):
        closed, seen = [], []

        class Manager:
            def create_worker_api_client(self):
                return object()

            def close_worker_api_client(self, client):
                closed.append(client)

            def cache_search_image(self, kind, key, url, api_client=None):
                return "" if key == "bad" else f"/cache/{key}.jpg"

        jobs = [("video", "a", "u"), ("video", "bad", "u"), ("author", "c", "u")]
        cache_search_images(Manager(), jobs, lambda *args: seen.append(args), concurrency=2, should_stop=lambda: False)
        self.assertEqual(sorted(seen), [("author", "c", "/cache/c.jpg"), ("video", "a", "/cache/a.jpg")])
        self.assertTrue(closed)


if __name__ == "__main__":
    unittest.main()


class SearchOptionsTests(unittest.TestCase):
    def test_sorts_follow_the_endpoint(self):
        from app.presentation.search import options

        keys = lambda *a: [key for _label, key in options.sort_choices(*a)]  # noqa: E731
        self.assertEqual(keys("iwara", "videos", "dance"), ["date", "relevance", "views", "likes"])
        self.assertEqual(keys("iwara", "authors", "ann"), ["date", "relevance"])
        self.assertIn("trending", keys("iwara", "videos", ""))
        self.assertIn("trending", keys("oreno3d", "videos", "dance"))

    def test_oreno3d_only_answers_videos_and_tags(self):
        from app.presentation.search import options

        self.assertEqual(options.supported_scopes("oreno3d"), {"videos", "tags"})
        self.assertIn("playlists", options.supported_scopes("iwara"))

    def test_found_text_mentions_the_filtered_count(self):
        from app.presentation.search import options

        with_filter = options.found_text("videos", videos=22, authors=0, playlists=0, total=300, shown=18)
        self.assertIn("22", with_filter)
        self.assertIn("18", with_filter)
        self.assertNotIn("18", options.found_text("videos", videos=22, authors=0, playlists=0, total=None))
        self.assertIn("3", options.found_text("authors", videos=0, authors=3, playlists=0, total=None))
