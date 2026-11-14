from __future__ import annotations

import os
import threading
import unittest
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


if __name__ == "__main__":
    unittest.main()
