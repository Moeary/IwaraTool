"""Home page: rating filter, feed requests, cards, navigation and detail view."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest import mock
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from app.core import rating
from app.core.api import IwaraAPI
from app.core.home_cache import HomeFeedCache
from app.core.home_feed import default_specs, home_sections
from app.core.search import (
    SearchFilters,
    SearchVideo,
    build_image_query_params,
    build_video_query_params,
    filter_videos,
    normalize_image,
    small_cover_url,
)
from app.ui.home_workers import normalize_items


def _video(video_id="a", **kwargs) -> SearchVideo:
    return SearchVideo(video_id=video_id, title=f"title {video_id}", **kwargs)


class RatingTests(unittest.TestCase):
    def test_normalize_accepts_site_and_user_wording(self):
        self.assertEqual(rating.normalize_rating("SFW"), "general")
        self.assertEqual(rating.normalize_rating("nsfw"), "ecchi")
        self.assertEqual(rating.normalize_rating("R-18"), "ecchi")
        self.assertEqual(rating.normalize_rating("ecchi"), "ecchi")
        for junk in ("", None, "bogus", "all"):
            self.assertEqual(rating.normalize_rating(junk), "all")

    def test_api_value_is_empty_for_both(self):
        self.assertEqual(rating.api_rating("all"), "")
        self.assertEqual(rating.api_rating("sfw"), "general")
        self.assertEqual(rating.api_rating("nsfw"), "ecchi")

    def test_unrated_items_only_pass_the_all_filter(self):
        self.assertTrue(rating.matches_rating("", "all"))
        self.assertFalse(rating.matches_rating("", "general"))
        self.assertTrue(rating.matches_rating("ecchi", "ecchi"))
        self.assertFalse(rating.matches_rating("general", "ecchi"))

    def test_filter_by_rating(self):
        items = [_video("a", rating="general"), _video("b", rating="ecchi"), _video("c")]
        self.assertEqual([v.video_id for v in rating.filter_by_rating(items, "sfw")], ["a"])
        self.assertEqual([v.video_id for v in rating.filter_by_rating(items, "nsfw")], ["b"])
        self.assertEqual(len(rating.filter_by_rating(items, "all")), 3)

    def test_local_search_filter_honours_the_rating(self):
        items = [_video("a", rating="general"), _video("b", rating="ecchi")]
        kept = filter_videos(items, SearchFilters(rating="ecchi"))
        self.assertEqual([v.video_id for v in kept], ["b"])
        self.assertEqual(len(filter_videos(items, SearchFilters())), 2)

    def test_browse_params_send_the_rating_to_the_server(self):
        self.assertEqual(build_video_query_params(SearchFilters(rating="general"))["rating"], "general")
        self.assertNotIn("rating", build_video_query_params(SearchFilters()))
        params = build_image_query_params(SearchFilters(sort="trending", rating="ecchi"))
        self.assertEqual((params["sort"], params["rating"]), ("trending", "ecchi"))
        self.assertNotIn("tags", params)


class FeedDefinitionTests(unittest.TestCase):
    def test_sections_cover_subscriptions_and_hot_lists(self):
        sections = {s.id: s for s in home_sections(default_specs())}
        self.assertEqual(list(sections), ["subscriptions", "hot_videos", "hot_images"])
        subs = sections["subscriptions"]
        self.assertTrue(all(tab.needs_login for tab in subs.tabs))
        self.assertEqual({tab.kind for tab in subs.tabs}, {"video", "image"})
        self.assertEqual(dict(subs.tab("videos").params), {"subscribed": "true", "sort": "date"})
        self.assertEqual([t.id for t in sections["hot_images"].tabs], ["trending", "popularity", "date"])
        self.assertTrue(all(t.kind == "image" for t in sections["hot_images"].tabs))

    def test_request_params_add_the_rating_only_when_filtering(self):
        tab = {s.id: s for s in home_sections(default_specs())}["hot_videos"].tab("trending")
        self.assertEqual(tab.request_params("all"), {"sort": "trending"})
        self.assertEqual(tab.request_params("nsfw"), {"sort": "trending", "rating": "ecchi"})
        self.assertEqual(tab.request_params("sfw"), {"sort": "trending", "rating": "general"})

    def test_unknown_tab_falls_back_to_the_first(self):
        section = {s.id: s for s in home_sections(default_specs())}["hot_videos"]
        self.assertEqual(section.tab("nope").id, section.tabs[0].id)


class ApiTests(unittest.TestCase):
    def _api(self, payload):
        api = object.__new__(IwaraAPI)
        api._get_json = Mock(return_value=payload)
        return api

    def test_images_browse_uses_the_images_endpoint_and_page_sentinel(self):
        rows = [{"id": str(i)} for i in range(12)]
        api = self._api({"results": rows, "count": 13, "limit": 12})
        items, total, has_more, error = api.get_images_page({"sort": "trending", "rating": "general"}, page=0, limit=12)
        url = api._get_json.call_args.args[0]
        params = api._get_json.call_args.kwargs["params"]
        self.assertTrue(url.endswith("/images"))
        self.assertEqual(params["rating"], "general")
        self.assertEqual((len(items), has_more, error), (12, True, ""))
        self.assertIsNone(total)  # count is a next-page sentinel, not a total

    def test_related_and_comments_endpoints(self):
        api = self._api({"results": [{"id": "r1"}], "count": 5})
        rows, error = api.get_related("image", "abc", limit=6)
        self.assertEqual((rows, error), ([{"id": "r1"}], ""))
        self.assertTrue(api._get_json.call_args.args[0].endswith("/image/abc/related"))
        rows, total, error = api.get_comments("video", "abc", page=1, parent="c9")
        self.assertEqual((len(rows), total, error), (1, 5, ""))
        self.assertTrue(api._get_json.call_args.args[0].endswith("/video/abc/comments"))
        self.assertEqual(api._get_json.call_args.kwargs["params"]["parent"], "c9")
        self.assertEqual(api._get_json.call_args.kwargs["params"]["page"], "1")

    def test_failed_requests_return_an_error_instead_of_raising(self):
        api = object.__new__(IwaraAPI)
        api._get_json = Mock(side_effect=RuntimeError("HTTP 404: errors.notFound"))
        self.assertEqual(api.get_related("video", "x")[0], [])
        self.assertTrue(api.get_comments("video", "x")[2])
        self.assertIsNone(api.get_image_info("x")[0])
        self.assertIsNone(api.get_image_info("")[0])


class CardHelperTests(unittest.TestCase):
    def test_small_cover_only_rewrites_generated_video_thumbnails(self):
        big = "https://i.iwara.tv/image/original/abc/thumbnail-03.jpg"
        self.assertEqual(small_cover_url(big), "https://i.iwara.tv/image/thumbnail/abc/thumbnail-03.jpg")
        custom = "https://i.iwara.tv/image/original/abc/cover.png"
        self.assertEqual(small_cover_url(custom), custom)
        self.assertEqual(small_cover_url(""), "")

    def test_dead_imported_images_are_dropped_from_home_lists(self):
        rows = [
            {"id": "dead", "numImages": 0, "thumbnail": {"id": "f1"}},
            {"id": "live", "numImages": 2, "thumbnail": {"id": "f2"}},
        ]
        self.assertEqual([v.video_id for v in normalize_items("image", rows)], ["live"])
        # Videos are never filtered by that rule.
        self.assertEqual(len(normalize_items("video", [{"id": "v", "numImages": 0}])), 1)

    def test_image_cards_are_not_downloadable(self):
        image = normalize_image({"id": "i", "numImages": 1, "thumbnail": {"id": "f"}})
        self.assertFalse(image.downloadable)


class WidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def test_grid_follows_width_and_selection(self):
        from app.ui.media_card import MediaGrid

        grid = MediaGrid(min_card_width=200, gap=10)
        grid.resize(640, 10)
        grid.set_videos([_video(str(i), downloadable=True) for i in range(7)])
        grid.show()
        self.app.processEvents()
        self.assertEqual(grid.columns_for_width(640), 3)
        self.assertEqual(len(grid.shown_videos()), 7)
        self.assertGreater(grid.height(), 0)
        widths = {card.width() for card in grid._cards}
        self.assertEqual(len(widths), 1)
        grid._cards[1].set_selected(True, emit=True)
        grid._cards[3].set_selected(True, emit=True)
        self.assertEqual([v.video_id for v in grid.selected_videos()], ["1", "3"])
        grid.clear_selection()
        self.assertEqual(grid.selected_videos(), [])

    def test_real_mouse_clicks_activate_select_and_open_the_menu(self):
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest

        from app.ui.media_card import MediaGrid

        grid = MediaGrid(min_card_width=200, gap=10)
        grid.resize(640, 10)
        grid.set_videos([_video("a"), _video("b")])
        grid.show()
        self.app.processEvents()
        activated = []
        grid.card_activated.connect(lambda video: activated.append(video.video_id))
        card = grid._cards[0]
        QTest.mouseClick(card, Qt.MouseButton.LeftButton, pos=QPoint(40, 40))
        self.assertEqual(activated, ["a"])
        # The tick in the top-right corner selects instead of opening.
        QTest.mouseClick(card, Qt.MouseButton.LeftButton, pos=card._check_rect().center())
        self.assertEqual(activated, ["a"])
        self.assertEqual([v.video_id for v in grid.selected_videos()], ["a"])
        # Ctrl+click toggles selection too.
        QTest.mouseClick(grid._cards[1], Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier, QPoint(40, 40))
        self.assertEqual(len(grid.selected_videos()), 2)
        self.assertEqual(activated, ["a"])

    def test_row_limit_shows_two_rows_of_cards(self):
        from app.ui.media_card import MediaGrid

        grid = MediaGrid(min_card_width=200, gap=10, max_rows=2)
        grid.resize(640, 10)
        grid.set_videos([_video(str(i)) for i in range(12)])
        grid.show()
        self.app.processEvents()
        self.assertEqual(len(grid.shown_videos()), 6)
        self.assertEqual(len(grid.videos()), 12)
        grid.resize(850, 10)  # four columns now fit
        self.app.processEvents()
        self.assertEqual(len(grid.shown_videos()), 8)

    def test_wrap_lines_elides_the_last_line(self):
        from PySide6.QtGui import QFontMetrics

        from app.ui.media_card import wrap_lines

        metrics = QFontMetrics(self.app.font())
        lines = wrap_lines("word " * 80, metrics, 120, 2)
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[1].endswith("…"))
        self.assertEqual(wrap_lines("", metrics, 120, 2), [])
        self.assertEqual(wrap_lines("short", metrics, 400, 2), ["short"])


class HomeInterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        from app.ui import home_page

        self.home_page = home_page
        # Never read (or write) the developer's real layout and cache.
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        for target, value in (
            ("app.core.home_feed.load_specs", lambda store=None: default_specs()),
            ("app.core.home_cache._shared", HomeFeedCache(os.path.join(self._tmp.name, "cache.json"))),
        ):
            patch = mock.patch(target, value)
            patch.start()
            self.addCleanup(patch.stop)
        patcher = mock.patch("app.ui.home_workers.FeedWorker.run", lambda self: None)
        patcher.start()
        self.addCleanup(patcher.stop)
        detail = mock.patch("app.ui.media_detail.DetailWorker.run", lambda self: None)
        detail.start()
        self.addCleanup(detail.stop)
        self.home = home_page.HomeInterface()
        self.addCleanup(self._discard_home)

    def _discard_home(self):
        self.home.shutdown(timeout_ms=1000)
        self.home.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_queueable_ids_skip_images_and_embeds(self):
        ids = self.home_page.queueable_ids([
            _video("ok", source_kind="iwara", downloadable=True),
            _video("ok", source_kind="iwara", downloadable=True),
            _video("img", source_kind="iwara_image", downloadable=False),
            _video("yt", source_kind="iwara", downloadable=False),
        ])
        self.assertEqual(ids, ["ok"])

    def test_back_trail_returns_through_detail_pages(self):
        home = self.home
        home.show_detail("video", "a")
        home.show_detail("image", "b")
        self.assertEqual(home._stack.currentIndex(), home._DETAIL)
        home.go_back()
        self.assertEqual(home._trail[-1], ("detail", "video", "a"))
        home.go_back()
        self.assertEqual(home._stack.currentIndex(), home._FEED)
        home.go_back()  # nothing left to pop
        self.assertEqual(home._stack.currentIndex(), home._FEED)

    def test_more_on_a_ranking_jumps_to_the_search_page(self):
        from app.signal_bus import signal_bus

        requests = []

        def collect(request):
            requests.append(request)

        signal_bus.search_requested.connect(collect)
        self.addCleanup(signal_bus.search_requested.disconnect, collect)
        self.home._open_browse("hot_videos", "popularity")
        self.home._open_browse("hot_images", "date")
        self.assertEqual(
            requests,
            [{"scope": "videos", "sort": "popularity"}, {"scope": "images", "sort": "date"}],
        )
        self.assertEqual(self.home._stack.currentIndex(), self.home._FEED)

    def test_more_on_subscriptions_opens_browse_and_back_returns_to_feed(self):
        home = self.home
        home._open_browse("subscriptions", "images")
        self.assertEqual(home._stack.currentIndex(), home._BROWSE)
        self.assertEqual(home._browse._tab_id, "images")
        home.go_back()
        self.assertEqual(home._stack.currentIndex(), home._FEED)

    def test_detail_opened_elsewhere_returns_to_the_caller(self):
        home = self.home
        returned = []
        home.return_requested.connect(lambda: returned.append(True))
        home.show_detail("video", "a", external=True)
        self.assertEqual(home._stack.currentIndex(), home._DETAIL)
        home.show_detail("video", "b")  # a related post
        home.go_back()
        self.assertEqual(returned, [])
        home.go_back()
        self.assertEqual(returned, [True])
        self.assertEqual(home._stack.currentIndex(), home._FEED)

    def test_queue_uses_the_given_rule_and_ignores_images(self):
        home = self.home
        manager = Mock()
        manager.enqueue_video_ids.return_value = 1
        with mock.patch.object(self.home_page, "download_manager", manager), \
                mock.patch.object(self.home_page.InfoBar, "success"), \
                mock.patch.object(self.home_page.InfoBar, "warning") as warning:
            home._queue([_video("v1", source_kind="iwara", downloadable=True)], "rule-7")
            manager.enqueue_video_ids.assert_called_once()
            self.assertEqual(manager.enqueue_video_ids.call_args.args[0], ["v1"])
            self.assertEqual(manager.enqueue_video_ids.call_args.kwargs["rule_id"], "rule-7")
            home._queue([_video("i1", source_kind="iwara_image", downloadable=False)])
            warning.assert_called_once()
            self.assertEqual(manager.enqueue_video_ids.call_count, 1)

    def test_subscription_row_asks_to_sign_in_when_logged_out(self):
        block = next(b for b in self.home._feed.blocks if b.section.id == "subscriptions")
        with mock.patch.object(self.home_page, "download_manager") as manager:
            manager.is_logged_in.return_value = False
            block.reload(force=True)
        self.assertFalse(block._login_btn.isHidden())
        self.assertIsNone(block._worker)

    def test_rating_choice_is_broadcast_and_applied(self):
        home = self.home
        seen = []
        from app.signal_bus import signal_bus

        def record(value):
            seen.append(value)

        signal_bus.content_rating_changed.connect(record)
        self.addCleanup(lambda: signal_bus.content_rating_changed.disconnect(record))
        with mock.patch.object(self.home_page.app_config, "set_ui_value"):
            home._choose_rating("nsfw")
        self.assertEqual(seen, ["ecchi"])
        self.assertEqual(home._rating, "ecchi")
        self.assertTrue(all(block._rating == "ecchi" for block in home._feed.blocks))


class DetailViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def test_related_posts_follow_the_rating_choice(self):
        from app.ui import media_detail
        from app.ui.home_workers import CoverFetcher

        view = media_detail.DetailView(CoverFetcher())
        self.addCleanup(view.deleteLater)
        view._token = 3
        related = [_video("s", rating="general"), _video("n", rating="ecchi")]
        with mock.patch.object(media_detail.app_config, "get_ui_value", return_value="general"):
            view._on_related(3, related)
        self.assertEqual([v.video_id for v in view._related_grid.videos()], ["s"])
        with mock.patch.object(media_detail.app_config, "get_ui_value", return_value="all"):
            view._on_related(3, related)
        self.assertEqual(len(view._related_grid.videos()), 2)
        view._on_related(2, [])  # a stale answer is ignored
        self.assertEqual(len(view._related_grid.videos()), 2)

    def test_stage_and_gallery_clicks_are_delivered(self):
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest

        from app.ui.media_detail import GalleryImage, MediaStage

        stage = MediaStage()
        self.addCleanup(stage.deleteLater)
        stage.resize(640, 360)
        stage.show()
        clicks = []
        stage.clicked.connect(lambda: clicks.append("stage"))
        QTest.mouseClick(stage, Qt.MouseButton.LeftButton, pos=QPoint(50, 50))
        self.assertEqual(clicks, [])  # not playable yet
        stage.set_playable(True)
        QTest.mouseClick(stage, Qt.MouseButton.LeftButton, pos=QPoint(50, 50))
        self.assertEqual(clicks, ["stage"])

        image = GalleryImage({"id": "f1", "name": "f1.png", "width": 100, "height": 50})
        self.addCleanup(image.deleteLater)
        image.resize(400, 200)
        image.show()
        opened = []
        image.clicked.connect(opened.append)
        QTest.mouseClick(image, Qt.MouseButton.LeftButton, pos=QPoint(20, 20))
        self.assertEqual(opened, ["https://i.iwara.tv/image/original/f1/f1.png"])

    def test_linkify_escapes_html_and_links_urls(self):
        from app.ui.media_detail import linkify

        text = linkify("<b>hi</b>\nsee https://example.com/a?b=1&c=2")
        self.assertNotIn("<b>", text)
        self.assertIn("&lt;b&gt;", text)
        self.assertIn("<br>", text)
        self.assertIn('<a href="https://example.com/a?b=1&amp;c=2">', text)


class AuthorRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def test_author_pages_open_in_the_subscription_page_and_back_returns(self):
        from app.signal_bus import signal_bus
        from app.ui.main_window import MainWindow

        with mock.patch("app.ui.home_workers.FeedWorker.run", lambda self: None):
            previous = MainWindow._window_ref
            window = MainWindow()
            try:
                window.switchTo(window._search_page)
                with mock.patch.object(window._subscription_page, "show_author") as show:
                    signal_bus.author_page_requested.emit(("alice", "Alice", "u1", ""))
                show.assert_called_once_with(("alice", "Alice", "u1", ""), external=True)
                self.assertIs(window.stackedWidget.currentWidget(), window._subscription_page)
                window._subscription_page.return_requested.emit()
                self.assertIs(window.stackedWidget.currentWidget(), window._search_page)
                # asked from the subscription page itself: nothing to return to
                window.switchTo(window._subscription_page)
                with mock.patch.object(window._subscription_page, "show_author") as show:
                    signal_bus.author_page_requested.emit(("bob", "Bob", "", ""))
                show.assert_called_once_with(("bob", "Bob", "", ""), external=False)
            finally:
                window._reloading_language = True
                window.close()
                window.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                MainWindow._window_ref = previous


class NavigationOrderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def test_sidebar_order(self):
        from app.ui.main_window import MainWindow

        with mock.patch("app.ui.home_workers.FeedWorker.run", lambda self: None):
            previous = MainWindow._window_ref
            window = MainWindow()
            try:
                order = [
                    window.stackedWidget.widget(i)
                    for i in range(window.stackedWidget.count())
                ]
                top = [
                    window._home_page,
                    window._subscription_page,
                    window._search_page,
                    window._download_page,
                    window._repair_page,
                    window._history_page,
                ]
                self.assertEqual([w for w in order if w in top], top)
                self.assertIs(window.stackedWidget.currentWidget(), window._home_page)
            finally:
                window._reloading_language = True
                window.close()
                window.deleteLater()
                # A live window keeps listening on the shared signal bus and
                # would leak into later suites (e.g. duplicate refresh calls).
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                MainWindow._window_ref = previous


if __name__ == "__main__":
    unittest.main()
