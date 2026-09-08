import os
import json
import shutil
import tempfile
import unittest

from app.core.subscriptions import SubscriptionStore
from app.core.manager import DownloadManager


class SubscriptionSourceOriginTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="iwaratool-source-origin-")
        self.store = SubscriptionStore(os.path.join(self.temp_dir, "history.db"))

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_account_import_promotes_existing_local_author_in_place(self):
        local_id = self.store.add_source("author", "Alice", "Local Alice")
        self.store.upsert_items(
            local_id,
            [{"video_id": "keep-video", "title": "Keep this cache"}],
        )

        account_id = self.store.add_source(
            "author",
            "alice",
            "Account Alice",
            "remote-alice",
            source_origin="account",
        )

        self.assertEqual(account_id, local_id)
        source = self.store.list_sources()[0]
        self.assertEqual(source["source_origin"], "account")
        self.assertEqual(source["title"], "Account Alice")
        self.assertEqual(self.store.list_items(local_id)[0]["video_id"], "keep-video")

    def test_source_origin_defaults_match_source_type(self):
        self.store.add_source("feed", "subscribed", "Account Feed")
        self.store.add_source("playlist", "playlist01", "Playlist")
        self.store.add_source("author", "author01", "Author")

        origins = {
            row["source_type"]: row["source_origin"]
            for row in self.store.list_sources()
        }
        self.assertEqual(origins, {
            "author": "local",
            "feed": "account",
            "playlist": "playlist",
        })

    def test_refresh_without_cover_preserves_resolved_url(self):
        source_id = self.store.add_source("author", "cover-author", "Cover Author")
        url = "https://files.iwara.tv/image/original/file/thumbnail-00.jpg"
        self.store.upsert_items(source_id, [{"video_id": "cover", "thumbnail_url": url}])
        for state in ({}, {"download_state_known": True, "download_state": "private"}):
            with self.subTest(state=state):
                self.store.upsert_items(source_id, [{"video_id": "cover", **state}])
                self.assertEqual(self.store.list_items(source_id)[0]["thumbnail_url"], url)
        replacement = url.replace("00.jpg", "01.jpg")
        self.store.upsert_items(source_id, [{"video_id": "cover", "thumbnail_url": replacement}])
        self.assertEqual(self.store.list_items(source_id)[0]["thumbnail_url"], replacement)

    def test_update_item_thumbnail_url_persists_resolved_detail_url(self):
        source_id = self.store.add_source("author", "author01", "Author")
        self.store.upsert_items(
            source_id,
            [{"video_id": "cover01", "title": "Cover"}],
        )

        url = "https://files.iwara.tv/image/original/file-01/thumbnail-00.jpg"
        self.store.update_item_thumbnail_url("cover01", url)

        self.assertEqual(self.store.list_items(source_id)[0]["thumbnail_url"], url)

    def test_manager_author_subscription_preserves_result_metadata(self):
        class _FakeSubscriptions:
            def __init__(self):
                self.call = None

            def add_source(self, *args, **kwargs):
                self.call = (args, kwargs)
                return 17

        subscriptions = _FakeSubscriptions()
        manager = DownloadManager.__new__(DownloadManager)
        manager.subscriptions = subscriptions

        source_id = manager.add_author_subscription(
            "@creator/",
            title="Creator Display",
            remote_id="user-1",
            avatar_url="https://img/avatar.jpg",
        )

        self.assertEqual(source_id, 17)
        self.assertEqual(
            subscriptions.call,
            (
                ("author", "creator", "Creator Display", "user-1"),
                {"avatar_url": "https://img/avatar.jpg"},
            ),
        )

    def test_oreno_author_url_is_persisted_and_exported(self):
        source_id = self.store.add_source(
            "author",
            "flim13",
            "Flim13",
            "iwara-user",
            source_origin="oreno3d",
            source_url="https://oreno3d.com/authors/1411",
        )
        source = self.store.get_source(source_id)
        self.assertEqual(source["source_origin"], "oreno3d")
        self.assertEqual(source["source_url"], "https://oreno3d.com/authors/1411")
        with open(self.store.backup_path, "r", encoding="utf-8") as handle:
            backup = json.load(handle)
        self.assertEqual(backup["sources"][0]["source_url"], "https://oreno3d.com/authors/1411")

    def test_manager_author_subscription_forwards_durable_source_only_when_present(self):
        class _FakeSubscriptions:
            def __init__(self):
                self.call = None

            def add_source(self, *args, **kwargs):
                self.call = (args, kwargs)
                return 18

        subscriptions = _FakeSubscriptions()
        manager = DownloadManager.__new__(DownloadManager)
        manager.subscriptions = subscriptions
        source_id = manager.add_author_subscription(
            "flim13",
            title="Flim13",
            remote_id="iwara-user",
            source_url="https://oreno3d.com/authors/1411",
            source_origin="oreno3d",
        )
        self.assertEqual(source_id, 18)
        self.assertEqual(
            subscriptions.call,
            (
                ("author", "flim13", "Flim13", "iwara-user"),
                {
                    "avatar_url": "",
                    "source_url": "https://oreno3d.com/authors/1411",
                    "source_origin": "oreno3d",
                },
            ),
        )

    def test_source_import_time_is_persisted_and_restored_from_backup(self):
        db_path = os.path.join(self.temp_dir, "history.db")
        source_id = self.store.add_source("author", "time-author", "Time Author")
        source = self.store.get_source(source_id)
        self.assertTrue(source["created_at"])

        with open(self.store.backup_path, "r", encoding="utf-8") as handle:
            backup = json.load(handle)
        self.assertEqual(backup["version"], 3)
        self.assertEqual(backup["sources"][0]["created_at"], source["created_at"])

        # Simulate restoring the local source snapshot after the DB file was
        # replaced or lost.  The original import timestamp must survive.
        os.remove(db_path)
        restored_store = SubscriptionStore(db_path)
        restored = restored_store.list_sources()[0]
        self.assertEqual(restored["created_at"], source["created_at"])


if __name__ == "__main__":
    unittest.main()
