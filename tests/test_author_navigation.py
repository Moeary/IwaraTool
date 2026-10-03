import json
import os
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtCore import QCoreApplication, QEvent, QSettings
from PySide6.QtWidgets import QApplication

from app.config import app_config
from app.core.oreno3d import Oreno3DDetail, Oreno3DEntity, Oreno3DListing
from app.core.search import (
    SearchFilters,
    SearchVideo,
    build_video_query_params,
    normalize_author,
    normalize_video,
)
from app.core.search_manager import SearchManagerMixin
from app.ui.search_page import SearchInterface
from app.ui.search_workers import SearchAuthorProfileWorker, SearchOrenoAuthorWorker, SearchOrenoLinkWorker, SearchWorker


class AuthorSearchWorkerTests(unittest.TestCase):
    def test_author_video_query_uses_user_id_without_username_keyword(self):
        manager = Mock()
        manager.get_search_video_page.return_value = (
            [{"id": "work-1", "title": "Unrelated title", "user": {"id": "user-42", "username": "creator"}}],
            1,
            False,
            "",
        )
        results = []
        with patch("app.ui.search_page.download_manager", manager):
            worker = SearchWorker(
                SearchFilters(author_id="user-42", sort="likes"),
                "videos",
                2,
                1,
                source="iwara",
                replace_results=True,
            )
            worker.result_ready.connect(results.append)
            worker.run()

        params = manager.get_search_video_page.call_args.args[0]
        self.assertEqual(params["user"], "user-42")
        self.assertEqual(params["page"], "2")
        self.assertEqual(params["sort"], "likes")
        self.assertNotIn("q", params)
        self.assertEqual([video.video_id for video in results[0].videos], ["work-1"])

    def test_author_profile_worker_requires_a_real_user_id(self):
        manager = Mock()
        manager.get_search_user_profile.return_value = ({"user": {"username": "creator"}}, "")
        results = []
        with patch("app.ui.search_page.download_manager", manager):
            worker = SearchAuthorProfileWorker(4, 7, ("creator", "Creator", "", ""))
            worker.result_ready.connect(results.append)
            worker.run()

        manager.get_search_user_profile.assert_called_once_with("creator")
        self.assertIsNone(results[0]["author"])
        self.assertTrue(results[0]["error"])

    def test_author_profile_worker_returns_profile_id(self):
        manager = Mock()
        manager.get_search_user_profile.return_value = (
            {"user": {"id": "real-id", "username": "creator", "name": "Creator"}},
            "",
        )
        results = []
        with patch("app.ui.search_page.download_manager", manager):
            worker = SearchAuthorProfileWorker(4, 7, ("creator", "Creator", "", ""))
            worker.result_ready.connect(results.append)
            worker.run()

        self.assertEqual(results[0]["author"].author_id, "real-id")
        self.assertEqual(results[0]["action_generation"], 7)
        self.assertEqual(results[0]["error"], "")

    def test_oreno_author_worker_keeps_source_url_after_iwara_hydration(self):
        manager = Mock()
        manager.resolve_oreno3d_author.return_value = {"oreno_author_url": "https://oreno3d.com/authors/123"}
        video = SearchVideo(
            "oreno3d:source-1",
            "Title",
            source_kind="iwara",
            source_url="https://www.iwara.tv/video/resolved-id",
            raw={"oreno3d_url": "https://oreno3d.com/movies/source-1"},
        )
        with patch("app.ui.search_page.download_manager", manager):
            worker = SearchOrenoAuthorWorker(1, video)
            worker.run()
        self.assertEqual(manager.resolve_oreno3d_author.call_args.args, ("source-1", "https://oreno3d.com/movies/source-1"))


class OrenoAuthorFallbackTests(unittest.TestCase):
    def setUp(self):
        self.manager = SearchManagerMixin()
        self.manager._api_lock = threading.RLock()
        self.manager.api = SimpleNamespace(scraper=Mock())
        self.manager._api_call = Mock(side_effect=AssertionError("An Oreno display name must not be queried as an Iwara account"))
        self.manager.get_iwara_video_info = Mock()
        self.author = Oreno3DEntity("123", "Source display name", "https://oreno3d.com/authors/123")

    def detail(self, source_id, iwara_id):
        return Oreno3DDetail(
            source_id,
            f"https://oreno3d.com/movies/{source_id}",
            "Source title",
            f"https://www.iwara.tv/video/{iwara_id}",
            self.author,
        )

    @staticmethod
    def listing(source_id):
        return Oreno3DListing(source_id, f"https://oreno3d.com/movies/{source_id}", "Title", "Source display name", "", 0, 0)

    def test_saved_iwara_mapping_avoids_remote_lookup(self):
        mapping = {"id": "saved-id", "username": "saved-account", "name": "Saved author"}
        with patch("app.core.search_manager.Oreno3DClient") as client:
            result = self.manager.resolve_oreno3d_author(
                "dead-source",
                "https://oreno3d.com/movies/dead-source",
                author_url=self.author.url,
                iwara_author=mapping,
            )
        client.assert_not_called()
        self.assertEqual(result["iwara_author"]["id"], "saved-id")
        self.assertEqual(result["oreno_author_url"], self.author.url)
        self.manager._api_call.assert_not_called()

    def test_saved_author_page_skips_original_and_tries_other_works(self):
        client = Mock()
        client.fetch_author_page.return_value = ([self.listing("old"), self.listing("alive")], 1)
        client.fetch_detail.side_effect = [self.detail("old", "deleted-id"), self.detail("alive", "alive-id")]
        self.manager.get_iwara_video_info.side_effect = [
            (None, "Video not found"),
            ({"id": "alive-id", "user": {"id": "user-42", "username": "real-account"}}, ""),
        ]
        with patch("app.core.search_manager.Oreno3DClient", return_value=client):
            result = self.manager.resolve_oreno3d_author(
                "unavailable-source",
                "https://oreno3d.com/movies/unavailable-source",
                author_url=self.author.url,
                author_name="Source display name",
            )
        client.fetch_detail_url.assert_not_called()
        self.assertEqual(result["iwara_author"]["username"], "real-account")
        self.assertEqual(self.manager.get_iwara_video_info.call_count, 2)
        self.manager._api_call.assert_not_called()

    def test_deleted_original_video_falls_back_to_author_page(self):
        client = Mock()
        client.fetch_detail_url.return_value = self.detail("original", "deleted-id")
        client.fetch_author_page.return_value = ([self.listing("alive")], 1)
        client.fetch_detail.return_value = self.detail("alive", "alive-id")
        self.manager.get_iwara_video_info.side_effect = [
            (None, "Video not found"),
            ({"id": "alive-id", "user": {"id": "user-42", "username": "real-account"}}, ""),
        ]
        with patch("app.core.search_manager.Oreno3DClient", return_value=client):
            result = self.manager.resolve_oreno3d_author("original", "https://oreno3d.com/movies/original")
        client.fetch_author_page.assert_called_once_with(self.author.url, page=1)
        self.assertEqual(result["iwara_author"]["id"], "user-42")

    def test_source_author_url_survives_when_no_iwara_account_can_be_found(self):
        client = Mock()
        client.fetch_author_page.side_effect = RuntimeError("Source temporarily unavailable")
        with patch("app.core.search_manager.Oreno3DClient", return_value=client):
            result = self.manager.resolve_oreno3d_author(
                "deleted-source",
                "https://oreno3d.com/movies/deleted-source",
                author_url=self.author.url,
                author_name="Source display name",
            )
        self.assertNotIn("iwara_author", result)
        self.assertEqual(result["oreno_author_url"], self.author.url)
        self.assertIn("unavailable", result["error"])
        self.manager._api_call.assert_not_called()


class AuthorNavigationPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = QSettings(os.path.join(self.temp.name, "settings.ini"), QSettings.Format.IniFormat)
        self.settings_patch = patch.object(app_config, "_qs", self.settings)
        self.settings_patch.start()
        app_config.search_auto_search_enabled = False
        app_config.ui_language = "zh_CN"
        self.manager = Mock()
        self.manager.history.get_records.return_value = {}
        self.manager.add_author_subscription.return_value = 42
        self.manager_patch = patch("app.ui.search_page.download_manager", self.manager)
        self.manager_patch.start()
        self.rules_patch = patch("app.ui.rules_page.RulePicker.refresh_rules")
        self.rules_patch.start()
        self.bus = Mock()
        self.bus_patch = patch("app.ui.search_page.signal_bus", self.bus)
        self.bus_patch.start()
        self.page = SearchInterface()
        self.search_patch = patch.object(self.page, "_run_search")
        self.run_search = self.search_patch.start()
        self.warning_patch = patch.object(self.page, "_show_warning")
        self.warning = self.warning_patch.start()
        self.author = normalize_author({"user": {"id": "user-42", "username": "creator", "name": "Creator"}})

    def tearDown(self):
        self.page._auto_search_timer.stop()
        self.page.shutdown(timeout_ms=1000)
        self.page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.warning_patch.stop()
        self.search_patch.stop()
        self.bus_patch.stop()
        self.rules_patch.stop()
        self.manager_patch.stop()
        self.settings_patch.stop()
        self.settings.sync()
        self.temp.cleanup()

    def selected(self, data):
        kind = "video" if isinstance(data, SearchVideo) else "author"
        return patch.object(self.page, "_selected_data", return_value=[{"kind": kind, "data": data}])

    def test_view_author_works_does_not_add_a_subscription(self):
        with self.selected(self.author):
            self.page._view_selected_author_works()
        filters = self.run_search.call_args.args[0]
        self.assertEqual(filters.author_id, "user-42")
        self.assertNotIn("q", build_video_query_params(filters))
        self.manager.add_author_subscription.assert_not_called()
        self.assertIn("@creator", self.page._scope_hint.text())
        self.assertIn("普通搜索", self.page._scope_hint.text())
        self.assertEqual(self.page._source_combo.currentData(), "iwara")
        self.assertEqual(self.page._scope_combo.currentData(), "videos")

    def test_video_author_works_uses_video_user_id(self):
        video = normalize_video({"id": "work-1", "title": "Title", "user": {"id": "user-42", "username": "creator"}})
        with self.selected(video):
            self.page._view_selected_author_works()
        self.assertEqual(self.run_search.call_args.args[0].author_id, "user-42")
        self.manager.add_author_subscription.assert_not_called()

    def test_missing_user_id_is_resolved_in_a_background_worker(self):
        author = normalize_author({"user": {"username": "creator", "name": "Creator"}})
        with self.selected(author), patch("app.ui.search_actions.SearchAuthorProfileWorker") as worker_type:
            worker = worker_type.return_value
            worker.isRunning.return_value = False
            self.page._view_selected_author_works()
            worker.start.assert_called_once_with()
            self.run_search.assert_not_called()
            self.manager.get_search_user_profile.assert_not_called()
            request_generation = worker_type.call_args.args[1]
            self.page._on_author_profile_result(
                {"generation": self.page._generation, "action_generation": request_generation, "author": self.author}
            )
            self.page._cleanup_author_profile_worker(worker)
        self.assertEqual(self.run_search.call_args.args[0].author_id, "user-42")

    def test_keyword_edit_invalidates_a_pending_profile_navigation(self):
        author = normalize_author({"user": {"username": "creator"}})
        with self.selected(author), patch("app.ui.search_actions.SearchAuthorProfileWorker") as worker_type:
            worker = worker_type.return_value
            worker.isRunning.return_value = False
            self.page._view_selected_author_works()
            request_generation = worker_type.call_args.args[1]
            self.page._keyword_edit.setText("new keyword")
            self.page._keyword_edit.textEdited.emit("new keyword")
            worker.requestInterruption.assert_called()
            self.page._on_author_profile_result(
                {"generation": self.page._generation, "action_generation": request_generation, "author": self.author}
            )
            self.page._cleanup_author_profile_worker(worker)
        self.run_search.assert_not_called()
        self.assertIsNone(self.page._author_video_target)

    def test_subscribe_and_go_invalidates_an_earlier_view_profile_request(self):
        author = normalize_author({"user": {"username": "creator"}})
        with (
            self.selected(author),
            patch("app.ui.search_actions.SearchAuthorProfileWorker") as worker_type,
            patch("app.ui.search_page.InfoBar.success"),
        ):
            worker = worker_type.return_value
            worker.isRunning.return_value = False
            self.page._view_selected_author_works()
            request_generation = worker_type.call_args.args[1]
            self.page._subscribe_selected_author_and_go()
            self.bus.subscription_source_requested.emit.assert_called_once_with(42)
            self.page._on_author_profile_result(
                {"generation": self.page._generation, "action_generation": request_generation, "author": self.author}
            )
            self.page._cleanup_author_profile_worker(worker)
        self.run_search.assert_not_called()

    def test_video_without_author_metadata_is_hydrated_before_viewing_works(self):
        video = SearchVideo("work-1", "Title")
        self.page._all_videos = [video]
        with self.selected(video), patch("app.ui.search_actions.SearchIwaraAuthorWorker") as worker_type:
            worker = worker_type.return_value
            worker.isRunning.return_value = False
            self.page._view_selected_author_works()
            worker.start.assert_called_once_with()
            self.manager.get_iwara_video_info.assert_not_called()
            self.page._on_iwara_author_result(
                {
                    "generation": self.page._generation,
                    "video_id": "work-1",
                    "metadata": {"id": "work-1", "title": "Title", "user": {"id": "user-42", "username": "creator"}},
                }
            )
            self.page._cleanup_iwara_author_worker(worker)
        self.assertEqual(self.run_search.call_args.args[0].author_id, "user-42")
        self.manager.add_author_subscription.assert_not_called()

    def test_pagination_and_sort_keep_author_and_keyword_edit_exits(self):
        with self.selected(self.author):
            self.page._view_selected_author_works()
        self.page._navigate_to_page(3)
        self.assertEqual(self.run_search.call_args.args[0].author_id, "user-42")
        self.assertEqual(self.run_search.call_args.kwargs["page"], 3)
        self.page._set_combo_data(self.page._sort_combo, "likes")
        self.page._start_search()
        self.assertEqual(self.run_search.call_args.args[0].author_id, "user-42")
        self.assertEqual(self.run_search.call_args.args[0].sort, "likes")
        self.page._keyword_edit.setText("new keyword")
        self.page._keyword_edit.textEdited.emit("new keyword")
        self.assertIsNone(self.page._author_video_target)
        self.assertEqual(self.page._build_filters().author_id, "")
        self.assertEqual(self.page._build_filters().keyword, "new keyword")

    def test_scope_source_history_and_reset_exit_author_search(self):
        changes = (
            lambda: self.page._set_combo_data(self.page._scope_combo, "tags"),
            lambda: self.page._set_combo_data(self.page._source_combo, "oreno3d"),
            lambda: self.page._apply_search_history(json.dumps([{"keyword": "dance", "source": "iwara", "scope": "videos", "sort": "date"}])),
            self.page._reset_filters,
        )
        for change in changes:
            with self.subTest(change=change):
                self.page._show_author_works_target(("creator", "Creator", "user-42", ""))
                change()
                self.app.processEvents()
                self.assertIsNone(self.page._author_video_target)
                self.assertEqual(self.page._build_filters().author_id, "")

    def test_source_author_page_only_has_feedback_and_an_explicit_browser_entry(self):
        video = SearchVideo(
            video_id="oreno3d:source-1",
            title="Source title",
            author_name="Source display name",
            source_kind="oreno3d",
            source_url="https://oreno3d.com/movies/source-1",
            raw={"oreno3d_url": "https://oreno3d.com/movies/source-1"},
        )
        self.page._all_videos = [video]
        self.page._pending_author_actions[video.video_id] = "view"
        with patch("app.ui.search_page.webbrowser.open") as open_browser:
            self.page._on_oreno_author_result(
                {
                    "generation": self.page._generation,
                    "video_id": video.video_id,
                    "result": {"oreno_author_url": "https://oreno3d.com/authors/123"},
                }
            )
            open_browser.assert_not_called()
            self.assertIn("打开作者页", self.warning.call_args.args[0])
            self.assertIsNone(self.page._author_video_target)
            self.manager.add_author_subscription.assert_not_called()
            with self.selected(video):
                self.page._open_author_page_for_result()
            open_browser.assert_called_once_with("https://oreno3d.com/authors/123")

    def test_bridge_display_name_is_never_used_as_an_iwara_account(self):
        video = SearchVideo(
            video_id="oreno3d:source-1",
            title="Source title",
            author_name="Source display name",
            source_kind="oreno3d",
            source_url="https://oreno3d.com/movies/source-1",
        )
        self.assertIsNone(self.page._author_navigation_target(video))

    def test_deleted_bridge_video_can_open_author_view_works_and_subscribe(self):
        video = SearchVideo(
            "oreno3d:source-1", "Old title", source_kind="oreno3d",
            source_url="https://oreno3d.com/movies/source-1", downloadable=False,
            raw={"oreno3d_url": "https://oreno3d.com/movies/source-1"},
        )
        source_author = Oreno3DEntity("123", "Source name", "https://oreno3d.com/authors/123")
        core = SearchManagerMixin()
        core.api = SimpleNamespace(scraper=SimpleNamespace(proxies={}))
        core._api_lock = threading.RLock()
        core.get_iwara_video_info = self.manager.get_iwara_video_info
        self.manager.get_iwara_video_info.side_effect = [
            (None, "HTTP 404: deleted"),
            ({"id": "alive", "user": {"id": "user-42", "username": "creator"}}, ""),
        ]
        self.manager.resolve_oreno3d_video_details.side_effect = core.resolve_oreno3d_video_details
        self.manager.resolve_oreno3d_author.side_effect = core.resolve_oreno3d_author
        client = Mock()
        client.fetch_detail_url.return_value = Oreno3DDetail(
            "source-1", video.source_url, "Old title", "https://www.iwara.tv/video/deleted", source_author,
        )
        client.fetch_author_page.return_value = (
            [Oreno3DListing("survivor", "https://oreno3d.com/movies/survivor", "Title", "Source name", "", 0, 0)], 1,
        )
        client.fetch_detail.return_value = Oreno3DDetail(
            "survivor", "https://oreno3d.com/movies/survivor", "Title", "https://www.iwara.tv/video/alive", source_author,
        )
        self.page._all_videos = [video]
        with (
            patch("app.core.search_manager.Oreno3DClient", return_value=client),
            patch("app.core.search_manager.cloudscraper.create_scraper"),
            patch.object(self.page, "_start_image_loading"),
        ):
            worker = SearchOrenoLinkWorker(self.page._generation, [video])
            worker.item_ready.connect(self.page._on_oreno_link_item)
            worker.run()
        client.fetch_author_page.assert_called_once_with(source_author.url, page=1)
        self.assertEqual(video.iwara_url, "https://www.iwara.tv/video/deleted")
        with self.selected(video), patch("app.ui.search_page.webbrowser.open") as browser:
            self.page._open_author_page_for_result()
            browser.assert_called_once_with(source_author.url)
            self.page._view_selected_author_works()
        self.assertEqual(self.run_search.call_args.args[0].author_id, "user-42")
        with self.selected(video), patch("app.ui.search_page.InfoBar.success"):
            self.page._subscribe_selected_author_and_go()
        self.assertEqual(self.manager.add_author_subscription.call_args.args[0], "creator")
        self.assertEqual(self.manager.add_author_subscription.call_args.kwargs["source_url"], source_author.url)
        self.bus.subscription_source_requested.emit.assert_called_once_with(42)
        self.warning.assert_not_called()

    def test_subscription_navigation_is_opt_in_after_source_added_notification(self):
        with patch("app.ui.search_page.InfoBar.success"):
            first = self.page._subscribe_to_author(("creator", "Creator", "user-42", ""))
            self.bus.subscription_source_requested.emit.assert_not_called()
            self.page._subscribe_to_author(("creator", "Creator", "user-42", ""), navigate=True)
        self.assertEqual(first, 42)
        self.assertEqual(self.bus.subscription_source_added.emit.call_count, 2)
        self.bus.subscription_source_requested.emit.assert_called_once_with(42)
        calls = self.bus.mock_calls
        added = next(index for index, call in enumerate(calls) if call[0] == "subscription_source_added.emit")
        requested = next(index for index, call in enumerate(calls) if call[0] == "subscription_source_requested.emit")
        self.assertLess(added, requested)


if __name__ == "__main__":
    unittest.main()
