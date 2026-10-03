"""Regression coverage for partial Oreno pages and unavailable Iwara works."""
import os
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from requests.exceptions import Timeout

from app.core.oreno3d import Oreno3DClient, extract_iwara_video_id, parse_detail_page
from app.core.search import SearchVideo
from app.core.search_manager import SearchManagerMixin
from app.ui.search_page import SearchInterface
from app.ui.search_workers import SearchOrenoAuthorWorker, SearchOrenoLinkWorker


SOURCE = "https://oreno3d.com/movies/123"
AUTHOR = "https://oreno3d.com/authors/456"
PROFILE = {"id": "user-42", "username": "verified-account", "name": "Author"}
# Minimized structure of /movies/117917: empty title, intact source and author.
PARTIAL_DETAIL = """
<article class="g-main-video-article">
  <header><h1 class="video-h1"></h1>
    <figure class="video-figure">
      <a class="pop_separate" href="https://www.iwara.tv/video/unavailable-id/title">Play</a>
    </figure>
  </header>
  <section class="video-section-tag">
    <a href="/authors/456"><span class="video-center">Source author</span></a>
  </section>
</article>
"""


def card(source_id="123"):
    return SearchVideo(
        f"oreno3d:{source_id}", "Source title", source_kind="oreno3d",
        source_url=SOURCE, downloadable=False, raw={"oreno3d_url": SOURCE},
    )


class PartialDetailTests(unittest.TestCase):
    def parse(self, html):
        return parse_detail_page(html, source_id="123", oreno3d_url=SOURCE)

    def test_empty_title_keeps_video_and_author_links(self):
        detail = self.parse(PARTIAL_DETAIL)
        self.assertEqual(extract_iwara_video_id(detail.external_video_url), "unavailable-id")
        self.assertEqual(detail.author.url, AUTHOR)
        self.assertEqual(detail.title, "123")

    def test_challenge_html_is_not_accepted_as_a_detail(self):
        with self.assertRaises(ValueError):
            self.parse("<h1>Please wait</h1>")

    def test_button_fallback_ignores_non_iwara_hosts_and_comment_links(self):
        detail = self.parse('''
        <h1 class="video-h1">Title</h1>
        <a href="https://www.iwara.tv/video/unrelated">Author comment</a>
        <a class="video-watch-btn2" href="https://example.com/iwara.tv/video/wrong">Ad</a>
        <a class="video-watch-btn2" href="//www.iwara.tv/video/right?x=1">Watch</a>
        ''')
        self.assertEqual(extract_iwara_video_id(detail.external_video_url), "right")
        self.assertEqual(extract_iwara_video_id("https://iwara.tv.example.com/video/wrong"), "")

    def test_empty_author_anchor_does_not_hide_valid_author(self):
        html = PARTIAL_DETAIL.replace(
            '<a href="/authors/456">', '<a href="/authors/0"></a><a href="/authors/456">',
        )
        self.assertEqual(self.parse(html).author.url, AUTHOR)

    def test_sidebar_author_and_play_link_do_not_override_main_record(self):
        html = '''<aside>
          <a href="/authors/999">Unrelated</a>
          <a class="video-watch-btn2" href="https://www.iwara.tv/video/wrong">Ad</a>
        </aside>''' + PARTIAL_DETAIL
        detail = self.parse(html)
        self.assertEqual(detail.author.url, AUTHOR)
        self.assertEqual(extract_iwara_video_id(detail.external_video_url), "unavailable-id")


class OrenoRequestRecoveryTests(unittest.TestCase):
    @staticmethod
    def response(status, **headers):
        return Mock(status_code=status, text=PARTIAL_DETAIL, headers=headers)

    def test_temporary_failure_retried_once_and_responses_closed(self):
        for failure in (self.response(503), self.response(429), Timeout("temporary timeout")):
            with self.subTest(failure=failure):
                success = self.response(200)
                session = Mock()
                session.get.side_effect = [failure, success]
                with patch("app.core.oreno3d.time.sleep"):
                    detail = Oreno3DClient(session).fetch_detail_url("123", SOURCE)
                self.assertEqual(detail.author.url, AUTHOR)
                self.assertEqual(session.get.call_count, 2)
                success.close.assert_called_once()
                if not isinstance(failure, Exception):
                    failure.close.assert_called_once()

    def test_terminal_errors_and_long_retry_after_are_not_retried(self):
        for response in (self.response(404), self.response(403), self.response(429, **{"Retry-After": "60"})):
            with self.subTest(status=response.status_code):
                session = Mock()
                session.get.return_value = response
                with self.assertRaisesRegex(RuntimeError, str(response.status_code)):
                    Oreno3DClient(session).fetch_detail_url("123", SOURCE)
                self.assertEqual(session.get.call_count, 1)
                response.close.assert_called_once()

    def test_repeated_failure_stops_after_two_attempts(self):
        session = Mock()
        session.get.side_effect = [Timeout("first"), Timeout("second")]
        with patch("app.core.oreno3d.time.sleep"), self.assertRaisesRegex(Timeout, "second"):
            Oreno3DClient(session).fetch_detail_url("123", SOURCE)
        self.assertEqual(session.get.call_count, 2)


class AuthorResolutionTests(unittest.TestCase):
    def setUp(self):
        self.manager = SearchManagerMixin()
        self.manager.api = SimpleNamespace(scraper=Mock())
        self.manager._api_lock = threading.RLock()
        self.manager.get_iwara_video_info = Mock()

    def test_known_selected_video_is_used_even_when_author_url_is_saved(self):
        self.manager.get_iwara_video_info.return_value = ({"user": PROFILE}, "")
        with patch("app.core.search_manager.Oreno3DClient") as client:
            result = self.manager.resolve_oreno3d_author(author_url=AUTHOR, iwara_video_id="alive")
        self.assertEqual(result["iwara_author"]["id"], "user-42")
        client.assert_not_called()

    def test_failure_reason_survives_when_all_candidate_works_are_unavailable(self):
        self.manager.get_iwara_video_info.return_value = (None, "HTTP 404: unavailable")
        client = Mock()
        client.fetch_author_page.return_value = ([object()], 1)
        client.fetch_detail.return_value = SimpleNamespace(external_video_url="https://www.iwara.tv/video/deleted")
        with patch("app.core.search_manager.Oreno3DClient", return_value=client):
            result = self.manager.resolve_oreno3d_author(author_url=AUTHOR)
        self.assertEqual(result["oreno_author_url"], AUTHOR)
        self.assertIn("404", result["error"])
        self.assertNotIn("iwara_author", result)

    def test_known_video_without_saved_author_still_fetches_source_author_page_link(self):
        self.manager.get_iwara_video_info.return_value = ({"user": PROFILE}, "")
        client = Mock()
        client.fetch_detail_url.return_value = parse_detail_page(PARTIAL_DETAIL, source_id="123", oreno3d_url=SOURCE)
        with patch("app.core.search_manager.Oreno3DClient", return_value=client):
            result = self.manager.resolve_oreno3d_author("123", SOURCE, iwara_video_id="alive")
        self.assertEqual(result["oreno_author_url"], AUTHOR)
        self.assertEqual(result["iwara_author"]["id"], "user-42")
        client.fetch_author_page.assert_not_called()

    def test_author_worker_passes_known_id_and_original_source(self):
        video = card()
        video.source_kind = "iwara"
        video.source_url = video.iwara_url = "https://www.iwara.tv/video/known-id"
        video.download_video_id = "known-id"
        manager = Mock()
        manager.resolve_oreno3d_author.return_value = {}
        with patch("app.ui.search_page.download_manager", manager):
            SearchOrenoAuthorWorker(1, video).run()
        call = manager.resolve_oreno3d_author.call_args
        self.assertEqual(call.args, ("123", SOURCE))
        self.assertEqual(call.kwargs["iwara_video_id"], "known-id")


class ResolutionPipelineTests(unittest.TestCase):
    def run_worker(self, videos, *, video_id="deleted", hydrate=True, author_found=True):
        manager = Mock()
        manager.resolve_oreno3d_video_details.return_value = {
            "video_id": video_id, "oreno_author_id": "456",
            "oreno_author_url": AUTHOR, "oreno_author_name": "Source author",
        }
        manager.get_iwara_video_info.return_value = (None, "HTTP 404: deleted")
        manager.resolve_oreno3d_author.return_value = {
            "oreno_author_url": AUTHOR,
            **({"iwara_author": PROFILE} if author_found else {"error": "All candidates unavailable"}),
        }
        items, results = [], []
        with patch("app.ui.search_page.download_manager", manager):
            worker = SearchOrenoLinkWorker(4, videos, concurrency=2, hydrate_metadata=hydrate)
            worker.item_ready.connect(items.append)
            worker.result_ready.connect(results.append)
            worker.run()
        return manager, items, results[0]

    def test_deleted_video_automatically_resolves_author_and_reuses_same_author_lookup(self):
        videos = [card("123"), card("124")]
        manager, items, result = self.run_worker(videos)
        manager.resolve_oreno3d_author.assert_called_once()
        self.assertEqual(manager.resolve_oreno3d_author.call_args.kwargs["author_url"], AUTHOR)
        self.assertEqual(len([item for item in items if item["stage"] == "author"]), 2)
        page = SearchInterface.__new__(SearchInterface)
        for video in videos:
            for item in items:
                if item["video_id"] == video.video_id:
                    page._apply_oreno_link(video, item["link"])
            self.assertEqual(video.iwara_url, "https://www.iwara.tv/video/deleted")
            self.assertEqual(video.raw["oreno3d_author_url"], AUTHOR)
            self.assertEqual(page._author_navigation_target(video)[:3], ("verified-account", "Author", "user-42"))
            self.assertIn("404", video.raw["iwara_metadata_error"])
        self.assertEqual(len(result["links"]), 2)

    def test_missing_video_link_still_resolves_source_author(self):
        video = card()
        manager, items, _ = self.run_worker([video], video_id="")
        manager.get_iwara_video_info.assert_not_called()
        page = SearchInterface.__new__(SearchInterface)
        for item in items:
            page._apply_oreno_link(video, item["link"])
        self.assertEqual(video.raw["oreno3d_author_url"], AUTHOR)
        self.assertEqual(page._author_navigation_target(video)[0], "verified-account")
        self.assertFalse(video.downloadable)
        self.assertFalse(video.iwara_url)

    def test_no_surviving_work_keeps_source_without_guessing_an_account(self):
        video = card()
        _, items, _ = self.run_worker([video], author_found=False)
        page = SearchInterface.__new__(SearchInterface)
        for item in items:
            page._apply_oreno_link(video, item["link"])
        self.assertEqual(video.raw["oreno3d_author_url"], AUTHOR)
        self.assertIsNone(page._author_navigation_target(video))
        self.assertIn("unavailable", video.raw["oreno3d_author_error"])

    def test_priority_id_only_request_does_not_wait_for_author(self):
        manager, items, _ = self.run_worker([card()], hydrate=False)
        manager.get_iwara_video_info.assert_not_called()
        manager.resolve_oreno3d_author.assert_not_called()
        self.assertEqual([item["stage"] for item in items], ["id"])

    def test_late_id_only_or_failed_response_keeps_loaded_author(self):
        page = SearchInterface.__new__(SearchInterface)
        video = card()
        page._apply_oreno_link(video, {"id": "alive", "metadata": {"id": "alive", "title": "Title", "user": PROFILE}})
        for late in ({"id": "alive", "metadata": {}}, {"id": "", "error": "timeout"}):
            page._apply_oreno_link(video, late)
        self.assertTrue(video.raw["_iwara_metadata_loaded"])
        self.assertEqual(page._author_navigation_target(video)[0], "verified-account")
        self.assertEqual(video.download_video_id, "alive")
        self.assertNotIn("oreno3d_resolution_error", video.raw)


if __name__ == "__main__":
    unittest.main()
