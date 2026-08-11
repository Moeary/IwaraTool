import os
import shutil
import tempfile
import unittest

from app.core.subscriptions import SubscriptionStore


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

    def test_update_item_thumbnail_url_persists_resolved_detail_url(self):
        source_id = self.store.add_source("author", "author01", "Author")
        self.store.upsert_items(
            source_id,
            [{"video_id": "cover01", "title": "Cover"}],
        )

        url = "https://files.iwara.tv/image/original/file-01/thumbnail-00.jpg"
        self.store.update_item_thumbnail_url("cover01", url)

        self.assertEqual(self.store.list_items(source_id)[0]["thumbnail_url"], url)


if __name__ == "__main__":
    unittest.main()
