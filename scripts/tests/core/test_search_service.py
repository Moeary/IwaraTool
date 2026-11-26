"""core.search.service.run_search without Qt: dispatch, rating scans, paging and errors."""

from __future__ import annotations

import asyncio
import unittest

from app.core.cancellation import CancelToken, cancel_scope
from app.core.search.models import SearchFilters
from app.core.search.service import RATING_SCAN_MAX_PAGES, run_search


def _video(index: int, rating: str = "general") -> dict:
    return {"id": f"v{index}", "title": f"Video {index}", "rating": rating, "user": {"username": "maker"}}


class FakeClient:
    """Records calls; every keyword page holds ``per_page`` ecchi rows and one general row."""

    def __init__(self, *, total=100, per_page=3, pages=10, fail=None):
        self.calls: list[tuple] = []
        self.total = total
        self.per_page = per_page
        self.pages = pages
        self.fail = fail

    def get_search_video_page(self, params, *, page, limit):
        self.calls.append(("videos", page))
        if self.fail:
            raise self.fail
        return [_video(page * 10 + i) for i in range(self.per_page)], self.total, True, ""

    def get_search_keyword_page(self, params, *, page, limit):
        self.calls.append(("keyword", page))
        rows = [_video(page * 10 + i, "ecchi") for i in range(self.per_page)] + [_video(page * 10 + 9)]
        return rows, None, page + 1 < self.pages, ""

    def get_oreno3d_search_page(self, keyword, *, page, sort, search_type, entity_id):
        self.calls.append(("oreno3d", page, keyword))
        return [], 3

    def get_search_user_profile(self, username):
        self.calls.append(("profile", username))
        return None, "not found"


class RunSearchTests(unittest.TestCase):
    def test_video_page_works_out_the_last_page_from_the_total(self):
        client = FakeClient(total=100)
        result = run_search(SearchFilters(page_size=32), "videos", 1, source="iwara", client=client)
        self.assertEqual(client.calls, [("videos", 1)])
        self.assertEqual([v.video_id for v in result.videos], ["v10", "v11", "v12"])
        self.assertEqual((result.current_page, result.next_page, result.last_page), (1, 2, 3))
        self.assertEqual(result.error, "")

    def test_failures_come_back_as_an_error_result(self):
        result = run_search(SearchFilters(), "videos", 0, source="iwara", client=FakeClient(fail=RuntimeError("offline")))
        self.assertEqual(result.error, "offline")
        self.assertIsNone(result.next_page)

    def test_rated_keyword_search_scans_until_enough_rows_or_the_page_cap(self):
        client = FakeClient()
        filters = SearchFilters(keyword="dance", rating="general")
        result = run_search(filters, "videos", 0, source="iwara", client=client)
        self.assertEqual(len(client.calls), RATING_SCAN_MAX_PAGES)
        self.assertTrue(all(v.rating == "general" for v in result.videos))
        self.assertEqual(result.next_page, RATING_SCAN_MAX_PAGES)  # continues after the last page read
        self.assertEqual(result.scanned_pages, RATING_SCAN_MAX_PAGES)

    def test_rating_scan_stops_when_asked(self):
        client = FakeClient()
        filters = SearchFilters(keyword="dance", rating="general")
        asked = iter([False, True])
        result = run_search(filters, "videos", 0, source="iwara", client=client, should_stop=lambda: next(asked))
        self.assertEqual(client.calls, [("keyword", 0)])
        self.assertEqual(result.next_page, 1)

    def test_rating_scan_follows_the_current_cancel_token_by_default(self):
        token = CancelToken()
        token.cancel()

        async def search():
            def work():
                with cancel_scope(token):
                    return run_search(SearchFilters(keyword="dance", rating="general"), "videos", 0, source="iwara", client=client)

            return await asyncio.to_thread(work)

        client = FakeClient()
        asyncio.run(search())
        self.assertEqual(client.calls, [])

    def test_oreno3d_pages_are_one_based_online(self):
        client = FakeClient()
        result = run_search(SearchFilters(keyword="miku"), "videos", 0, source="oreno3d", client=client)
        self.assertEqual(client.calls, [("oreno3d", 1, "miku")])
        self.assertEqual((result.next_page, result.last_page), (1, 2))

    def test_native_scopes_need_a_keyword(self):
        client = FakeClient()
        result = run_search(SearchFilters(), "playlists", 0, source="iwara", client=client)
        self.assertTrue(result.error)
        self.assertEqual(client.calls, [])

    def test_profile_reference_looks_up_the_user(self):
        client = FakeClient()
        result = run_search(
            SearchFilters(keyword="https://www.iwara.tv/profile/maker"), "authors", 0, source="iwara", client=client
        )
        self.assertEqual(client.calls, [("profile", "maker")])
        self.assertEqual(result.error, "not found")


if __name__ == "__main__":
    unittest.main()
