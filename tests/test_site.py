from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.config import app_config
from app.core import site as sites
from app.core.api.client import IwaraAPI


class _Response:
    def __init__(self, status: int, data):
        self.status_code = status
        self._data = data
        self.headers = {"content-type": "application/json"}
        self.text = str(data)

    def json(self):
        return self._data

    def close(self):
        pass


class SiteBasicsTests(unittest.TestCase):
    def setUp(self):
        self._saved = app_config.iwara_site
        self.addCleanup(setattr, app_config, "iwara_site", self._saved)
        app_config.iwara_site = "tv"

    def test_links_name_their_site(self):
        self.assertEqual(sites.site_of_url("https://www.iwara.ai/video/abc"), "ai")
        self.assertEqual(sites.site_of_url("iwara.ai/profile/someone"), "ai")
        self.assertEqual(sites.site_of_url("https://ecchi.iwara.tv/videos/x"), "tv")
        self.assertEqual(sites.site_of_url("https://www.iwara.tv/video/abc"), "tv")
        # Shared API / image hosts say nothing about the site.
        self.assertEqual(sites.site_of_url("https://api.iwara.tv/videos?sort=date"), "")
        self.assertEqual(sites.site_of_url("https://i.iwara.tv/image/x.jpg"), "")
        self.assertEqual(sites.site_of_url("https://example.com/iwara.ai"), "")
        self.assertEqual(sites.site_of_url("abc123"), "")

    def test_web_links_follow_the_site(self):
        self.assertEqual(sites.video_url("abc"), "https://www.iwara.tv/video/abc")
        self.assertEqual(sites.video_url("abc", "ai"), "https://www.iwara.ai/video/abc")
        self.assertEqual(sites.profile_url("a b", "ai"), "https://www.iwara.ai/profile/a%20b")
        self.assertEqual(sites.video_url(""), "")

    def test_current_site_follows_the_setting_and_scopes(self):
        self.assertEqual(sites.current_site(), "tv")
        app_config.iwara_site = "ai"
        self.assertEqual(sites.current_site(), "ai")
        with sites.site_scope("tv"):
            self.assertEqual(sites.current_site(), "tv")
            self.assertEqual(sites.api_headers()["X-Site"], "www.iwara.tv")
        self.assertEqual(sites.current_site(), "ai")
        with sites.site_scope(""):
            self.assertEqual(sites.current_site(), "ai")

    def test_scope_is_per_thread(self):
        seen = []
        with sites.site_scope("ai"):
            thread = threading.Thread(target=lambda: seen.append(sites.current_site()))
            thread.start()
            thread.join()
        self.assertEqual(seen, ["tv"])

    def test_unknown_values_mean_iwara_tv(self):
        self.assertEqual(sites.normalize("nonsense"), "tv")
        self.assertEqual(sites.normalize("iwara_ai"), "ai")
        self.assertEqual(sites.other("tv"), "ai")

    def test_items_carry_their_site(self):
        self.assertEqual(sites.site_of_item({"siteId": "iwara_ai"}), "ai")
        self.assertEqual(sites.site_of_item({"source_url": "https://www.iwara.ai/video/x"}), "ai")
        self.assertEqual(sites.site_of_item({}, default="ai"), "ai")
        self.assertEqual(sites.site_of_item({}), "tv")

    def test_different_site_body(self):
        self.assertEqual(sites.different_site({"message": "errors.differentSite", "siteId": "iwara_ai"}), "ai")
        self.assertEqual(sites.different_site({"message": "errors.notFound"}), "")

    def test_scoped_to_link(self):
        class Worker:
            @sites.scoped_to_link
            def run(self, link):
                return sites.current_site()

        self.assertEqual(Worker().run("https://www.iwara.ai/profile/x"), "ai")
        self.assertEqual(Worker().run("abc123"), "tv")


class ApiSiteTests(unittest.TestCase):
    def setUp(self):
        self._saved = app_config.iwara_site
        self.addCleanup(setattr, app_config, "iwara_site", self._saved)
        app_config.iwara_site = "tv"
        self.api = IwaraAPI()

    def test_headers_follow_the_scope(self):
        self.assertEqual(self.api._headers()["X-Site"], "www.iwara.tv")
        with sites.site_scope("ai"):
            headers = self.api._headers()
        self.assertEqual(headers["X-Site"], "www.iwara.ai")
        self.assertEqual(headers["Origin"], "https://www.iwara.ai")

    def test_a_post_on_the_other_site_is_fetched_there(self):
        calls = []
        replies = [
            _Response(301, {"message": "errors.differentSite", "siteId": "iwara_ai"}),
            _Response(200, {"id": "abc", "siteId": "iwara_ai"}),
        ]

        def fake_get(url, headers=None, **kwargs):
            calls.append(headers["X-Site"])
            return replies.pop(0)

        with mock.patch.object(self.api.scraper, "get", side_effect=fake_get):
            data, error = self.api.get_video_info("abc")
        self.assertEqual(error, "")
        self.assertEqual(data["siteId"], "iwara_ai")
        self.assertEqual(calls, ["www.iwara.tv", "www.iwara.ai"])

    def test_the_retry_happens_only_once(self):
        reply = _Response(301, {"message": "errors.differentSite", "siteId": "iwara_ai"})
        with mock.patch.object(self.api.scraper, "get", return_value=reply) as get:
            with self.assertRaises(RuntimeError):
                self.api._get_json("https://api.iwara.tv/video/abc")
        self.assertEqual(get.call_count, 2)


class LinkParsingTests(unittest.TestCase):
    def test_iwara_ai_links_are_parsed(self):
        from app.core.manager import download_manager

        parse = download_manager._parse_iwara_url
        self.assertEqual(parse("https://www.iwara.ai/video/abc/slug"), ("video", "abc"))
        self.assertEqual(parse("iwara.ai/profile/someone"), ("user", "someone"))
        self.assertEqual(parse("https://www.iwara.tv/playlist/p1"), ("playlist", "p1"))
        self.assertIsNone(parse("https://example.com/video/abc"))

    def test_search_results_link_to_their_site(self):
        from app.core.search.normalize import normalize_video

        video = normalize_video({"id": "abc", "title": "t", "siteId": "iwara_ai"})
        self.assertEqual(video.source_url, "https://www.iwara.ai/video/abc")
        video = normalize_video({"id": "abc", "title": "t", "siteId": "iwara"})
        self.assertEqual(video.source_url, "https://www.iwara.tv/video/abc")


class SiteSwitchTests(unittest.TestCase):
    def setUp(self):
        self._saved = app_config.iwara_site
        self.addCleanup(setattr, app_config, "iwara_site", self._saved)
        app_config.iwara_site = "tv"

    def test_switching_saves_and_announces(self):
        from app.signal_bus import signal_bus

        seen = []

        def record(key):
            seen.append(key)

        signal_bus.site_changed.connect(record)
        self.addCleanup(signal_bus.site_changed.disconnect, record)
        self.assertTrue(sites.set_configured_site("ai"))
        self.assertFalse(sites.set_configured_site("ai"))
        self.assertEqual(app_config.iwara_site, "ai")
        self.assertEqual(seen, ["ai"])

    def test_home_rows_are_cached_per_site(self):
        from app.core.home_cache import cache_key

        self.assertEqual(cache_key("latest", "videos", "all"), "latest|videos|all")
        self.assertEqual(cache_key("latest", "videos", "all", site="ai"), "ai|latest|videos|all")
        with sites.site_scope("ai"):
            self.assertEqual(cache_key("feed", "videos", "", account=True), "acct:ai|feed|videos|all")

    def test_a_worker_keeps_the_site_it_was_started_on(self):
        from PySide6.QtCore import QCoreApplication

        from app.ui.worker_lifecycle import ManagedThread

        QCoreApplication.instance() or QCoreApplication([])

        class Worker(ManagedThread):
            def run(self):
                self.seen = sites.current_site()

        worker = Worker()
        app_config.iwara_site = "ai"
        worker.start()
        app_config.iwara_site = "tv"  # switched while the request is out
        self.assertTrue(worker.wait(5000))
        self.assertEqual(worker.seen, "ai")

    def test_the_toggle_has_a_shortcut(self):
        from app.core.shortcut_defs import actions_by_id

        self.assertEqual(actions_by_id()["toggle_site"].default, "Ctrl+Shift+S")


class SubscriptionSiteTests(unittest.TestCase):
    def setUp(self):
        self._saved = app_config.iwara_site
        self.addCleanup(setattr, app_config, "iwara_site", self._saved)
        app_config.iwara_site = "tv"
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = os.path.join(self._tmp.name, "subscriptions.db")

    def _store(self):
        from app.core.subscription.store import SubscriptionStore

        return SubscriptionStore(self.db, legacy_db_path="")

    def test_old_databases_get_the_site_column(self):
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute(
                "CREATE TABLE sources (id INTEGER PRIMARY KEY AUTOINCREMENT, source_type TEXT NOT NULL, "
                "source_key TEXT NOT NULL, title TEXT DEFAULT '', remote_id TEXT DEFAULT '', enabled INTEGER DEFAULT 1, "
                "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
                "last_checked_at TEXT DEFAULT '', UNIQUE(source_type, source_key))"
            )
            conn.execute("INSERT INTO sources (id, source_type, source_key, title) VALUES (7, 'author', 'maker', 'Maker')")
            conn.commit()
        store = self._store()
        source = store.get_source(7)
        self.assertEqual(source["site"], "tv")
        self.assertEqual(source["title"], "Maker")
        # The same creator can now be followed on iwara.ai as well.
        ai_id = store.add_source("author", "maker", "Maker", site="ai")
        self.assertNotIn(ai_id, (0, 7))
        self.assertEqual(store.add_source("author", "MAKER", "Maker"), 7)

    def test_new_sources_take_the_site_of_their_link_or_the_current_one(self):
        store = self._store()
        tv_id = store.add_source("author", "maker")
        ai_link = store.add_source("author", "maker2", source_url="https://www.iwara.ai/profile/maker2")
        with sites.site_scope("ai"):
            ai_scoped = store.add_source("playlist", "p1")
        self.assertEqual(store.get_source(tv_id)["site"], "tv")
        self.assertEqual(store.get_source(ai_link)["site"], "ai")
        self.assertEqual(store.get_source(ai_scoped)["site"], "ai")

    def test_the_backup_remembers_the_site(self):
        store = self._store()
        store.add_source("author", "maker", site="ai")
        os.remove(self.db)
        restored = self._store()
        self.assertEqual([s["site"] for s in restored.list_sources()], ["ai"])

    def test_a_source_refreshes_on_its_own_site(self):
        from app.core.history import DownloadHistory
        from app.core.manager import DownloadManager

        mgr = DownloadManager()
        mgr.history = DownloadHistory(os.path.join(self._tmp.name, "history.db"))
        mgr.subscriptions = self._store()
        source_id = mgr.subscriptions.add_source("playlist", "p1", site="ai")
        seen = []

        def fake_api_call(method_name, *args, **_kwargs):
            seen.append((method_name, sites.current_site()))
            return []

        mgr._api_call = fake_api_call
        mgr.refresh_subscription_source(source_id)
        self.assertEqual(seen, [("get_playlist_videos", "ai")])

    def test_author_lookup_follows_the_current_site(self):
        from app.core.manager import DownloadManager

        mgr = DownloadManager()
        mgr.subscriptions = self._store()
        mgr.subscriptions.add_source("author", "maker", site="ai")
        self.assertIsNone(mgr.find_author_subscription("maker"))
        with sites.site_scope("ai"):
            self.assertEqual(mgr.find_author_subscription("Maker")["site"], "ai")


if __name__ == "__main__":
    unittest.main()
