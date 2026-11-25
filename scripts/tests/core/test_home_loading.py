"""core.home.loading without Qt: Home rows, details, comments and private clients."""

from __future__ import annotations

import unittest
from unittest.mock import Mock

from app.core.home import loading
from app.core.home.feed import MODE_AUTHOR, MODE_KEYWORD, MODE_TAGS


def _manager(client=None):
    manager = Mock()
    manager.create_worker_api_client.return_value = client or Mock()
    manager.get_home_page.return_value = ([{"id": "v1", "title": "One"}], 1, False, "")
    return manager


class FeedTests(unittest.TestCase):
    def setUp(self):
        loading._AUTHOR_IDS.clear()

    def test_tags_are_resolved_through_the_dictionary(self):
        manager = _manager()
        manager.tag_dictionary.resolve_query.return_value = ["hmv", "mmd"]
        result = loading.load_feed_page(manager, "video", {"tags": "hmv"}, 0, 24, mode=MODE_TAGS, token=7)
        self.assertEqual(manager.get_home_page.call_args.args[1]["tags"], "hmv,mmd")
        self.assertEqual((result.token, [v.video_id for v in result.items]), (7, ["v1"]))
        manager.close_worker_api_client.assert_called_once()

    def test_keyword_rows_are_rated_locally_but_all_kept_for_the_cache(self):
        client = Mock()
        client.search_page.return_value = (
            [{"id": "g", "rating": "general"}, {"id": "e", "rating": "ecchi"}], None, False, "",
        )
        result = loading.load_feed_page(
            _manager(client), "video", {"query": "miku"}, 0, 24, mode=MODE_KEYWORD, rating="general",
        )
        self.assertEqual([v.video_id for v in result.items], ["g"])
        self.assertEqual(len(result.rows), 2)

    def test_author_ids_are_remembered_for_the_session(self):
        client = Mock()
        client.get_user_id.return_value = ("uid-9", "")
        manager = _manager(client)
        loading.load_feed_page(manager, "video", {}, 0, 24, mode=MODE_AUTHOR, value="Alice")
        loading.load_feed_page(manager, "video", {}, 0, 24, mode=MODE_AUTHOR, value="@alice")
        client.get_user_id.assert_called_once_with("alice")
        self.assertEqual(manager.get_home_page.call_args.args[1]["user"], "uid-9")

    def test_errors_are_reported_and_the_client_is_closed(self):
        manager = _manager()
        manager.get_home_page.side_effect = RuntimeError("offline")
        result = loading.load_feed_page(manager, "image", {}, 2, 24)
        self.assertEqual((result.error, result.page, result.items), ("offline", 2, []))
        manager.close_worker_api_client.assert_called_once()

    def test_image_posts_without_files_are_dropped(self):
        rows = [{"id": "gone", "numImages": 0}, {"id": "live", "numImages": 2}]
        self.assertEqual([v.video_id for v in loading.normalize_items("image", rows)], ["live"])


class DetailTests(unittest.TestCase):
    def test_stages_arrive_in_order(self):
        client = Mock()
        client.get_video_info.return_value = ({"id": "v1"}, "")
        manager = _manager(client)
        manager.get_related_items.return_value = ([{"id": "v2"}], "")
        manager.get_item_comments.return_value = ([{"id": "c"}], 1, "")
        seen = []
        loading.load_detail(
            manager, "video", "v1",
            on_info=lambda info, error: seen.append(("info", info["id"])),
            on_related=lambda items: seen.append(("related", [v.video_id for v in items])),
            on_comments=lambda rows, total, error: seen.append(("comments", total)),
            should_stop=lambda: False,
        )
        self.assertEqual(seen, [("info", "v1"), ("related", ["v2"]), ("comments", 1)])

    def test_nothing_more_is_fetched_after_stopping(self):
        client = Mock()
        client.get_video_info.return_value = ({"id": "v1"}, "")
        manager = _manager(client)
        seen = []
        loading.load_detail(
            manager, "video", "v1",
            on_info=lambda *a: seen.append("info"),
            on_related=lambda *a: seen.append("related"),
            on_comments=lambda *a: seen.append("comments"),
            should_stop=lambda: True,
        )
        self.assertEqual(seen, [])
        manager.get_related_items.assert_not_called()
        manager.close_worker_api_client.assert_called_once()


class ClientCallTests(unittest.TestCase):
    def test_call_result_or_error(self):
        manager = _manager()
        self.assertEqual(loading.call_with_client(manager, lambda client: 42), (42, ""))

        def fail(_client):
            raise ValueError("bad")

        self.assertEqual(loading.call_with_client(manager, fail), (None, "bad"))
        self.assertEqual(manager.close_worker_api_client.call_count, 2)

    def test_comments(self):
        manager = _manager()
        manager.get_item_comments.return_value = ([{"id": "r"}], 3, "")
        self.assertEqual(loading.load_comments(manager, "image", "i1", page=1, parent="c"), ([{"id": "r"}], 3, ""))
        self.assertEqual(manager.get_item_comments.call_args.kwargs["parent"], "c")


if __name__ == "__main__":
    unittest.main()
