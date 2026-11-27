"""core.subscription.batch without Qt: source refresh, avatars and cover downloads."""

from __future__ import annotations

import unittest
from unittest.mock import Mock

from app.core.subscription.batch import cache_thumbnails, refresh_avatars, refresh_sources, source_id_list


class RefreshTests(unittest.TestCase):
    def test_source_id_list(self):
        self.assertEqual(source_id_list([3, 1, 3, 0]), [3, 1])
        self.assertEqual(source_id_list(5), [5])
        self.assertEqual(source_id_list(None), [])

    def test_selected_sources_refresh_in_order_with_progress_and_errors(self):
        manager = Mock()
        manager.subscriptions.get_source.side_effect = lambda sid: {"title": f"S{sid}"}
        manager.refresh_subscription_source.side_effect = [{"new": 2}, RuntimeError("HTTP 500")]
        manager._subscription_refresh_summary.side_effect = lambda summaries: {"summaries": summaries}
        events = []
        summary = refresh_sources(manager, [1, 2], ignore_disabled=True, on_progress=events.append)

        self.assertEqual(manager.refresh_subscription_source.call_args_list[0].kwargs, {"ignore_enabled": True})
        self.assertEqual([(e["stage"], e["source_id"]) for e in events],
                         [("started", 1), ("finished", 1), ("started", 2), ("finished", 2)])
        first, second = summary["summaries"]
        self.assertEqual((first["source_id"], first["title"], first["new"]), (1, "S1", 2))
        self.assertEqual((second["source_id"], second["error"]), (2, "HTTP 500"))

    def test_no_ids_refreshes_everything(self):
        manager = Mock()
        manager.refresh_all_subscriptions.return_value = {"all": True}
        self.assertEqual(refresh_sources(manager, []), {"all": True})


class AvatarTests(unittest.TestCase):
    def test_only_found_avatars_are_reported(self):
        manager = Mock()
        manager.refresh_subscription_source_avatar.side_effect = [
            {"source_id": 1, "avatar_url": "u1", "avatar_path": "/a1"},
            {"source_id": 2, "avatar_path": ""},
        ]
        seen = []
        refresh_avatars(manager, [1, 2], lambda *a: seen.append(a), should_stop=lambda: False)
        self.assertEqual(seen, [(1, "u1", "/a1")])


class ThumbnailTests(unittest.TestCase):
    def test_counts_paths_and_closes_every_client(self):
        manager = Mock()
        manager.create_worker_api_client.side_effect = lambda: Mock()
        manager.cache_subscription_thumbnail.side_effect = (
            lambda video_id, url, *, force, api_client: "" if video_id == "bad" else f"/cache/{video_id}"
        )
        ready = []
        counts = cache_thumbnails(
            manager, [("a", "u"), ("bad", "u"), ("c", "u")], lambda *a: ready.append(a),
            force=True, concurrency=2, should_stop=lambda: False,
        )
        self.assertEqual(counts, (2, 1))
        self.assertEqual(sorted(ready), [("a", "/cache/a"), ("c", "/cache/c")])
        self.assertEqual(manager.close_worker_api_client.call_count, manager.create_worker_api_client.call_count)
        self.assertTrue(manager.cache_subscription_thumbnail.call_args.kwargs["force"])

    def test_stopping_downloads_nothing(self):
        manager = Mock()
        counts = cache_thumbnails(manager, [("a", "u")], lambda *a: None, should_stop=lambda: True)
        manager.cache_subscription_thumbnail.assert_not_called()
        self.assertEqual(counts, (0, 0))


if __name__ == "__main__":
    unittest.main()
