"""Regression checks for remote text search, tag browsing, sort and paging."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QSettings
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.config import app_config
from app.core.api import IwaraAPI
from app.core.search import SearchFilters, SearchPageResult, build_keyword_query_params
from app.core.search_manager import SearchManagerMixin
from app.core.tag_dictionary import TagDictionary
from app.ui.search_page import SearchInterface, SearchWorker


def _set_columns(page, columns: int):
    """Pick the cover size that makes the grid lay out exactly ``columns`` per row."""

    spacing = 12
    available = max(1, page._results.viewport().width() - 16)
    page._card_min_width = max(40, (available + spacing) // columns - spacing)
    page._resize_grid()


class SearchRouteTests(unittest.TestCase):
    def setUp(self):
        self.context = ExitStack()
        self.addCleanup(self.context.close)
        self.root = Path(self.context.enter_context(tempfile.TemporaryDirectory()))
        cache = self.root / "tag_translations"
        cache.mkdir()
        (cache / "loveiwara_iwara_tags_localized.json").write_text(json.dumps({
            "hatsune_miku": {"en": "Hatsune Miku", "zh-CN": "初音未来", "ja": "初音ミク"},
            "genshin_impact": {"en": "Genshin Impact", "zh-CN": "原神", "ja": "原神"},
        }), encoding="utf-8")
        self.api = object.__new__(IwaraAPI)
        self.api._get_json = Mock(return_value={"results": [], "count": 0})
        self.manager = SearchManagerMixin()
        self.manager.tag_dictionary = TagDictionary(str(self.root))
        self.manager._api_call = lambda method, *args, **kwargs: getattr(self.api, method)(*args, **kwargs)
        self.context.enter_context(patch("app.ui.search_page.download_manager", self.manager))

    def search(self, keyword, *, scope="videos", sort="date", page=0, **options):
        results = []
        worker = SearchWorker(
            SearchFilters(keyword=keyword, sort=sort, **options), scope, page, 1,
            replace_results=True, source="iwara",
        )
        worker.result_ready.connect(results.append)
        worker.run()
        self.assertEqual(len(results), 1)
        return results[0]

    def test_keywords_use_search_for_every_sort_and_page_without_local_rematching(self):
        # A description-only hit and a title hit with fewer views deliberately
        # have an order that cannot be reconstructed from the displayed fields.
        raw = [
            {"id": "remote-first", "title": "Other title", "body": "舞蹈", "numViews": 1, "createdAt": "2024-01-01"},
            {"id": "remote-second", "title": "舞蹈", "numViews": 1000, "createdAt": "2026-01-01"},
        ]
        self.api._get_json.return_value = {"results": raw, "count": 90}
        keyword = '"舞蹈" -preview'
        for sort in ("relevance", "date", "views", "likes"):
            for page in (0, 1):
                with self.subTest(sort=sort, page=page):
                    result = self.search(keyword, sort=sort, page=page)
                    url = self.api._get_json.call_args.args[0]
                    params = self.api._get_json.call_args.kwargs["params"]
                    self.assertTrue(url.endswith("/search"))
                    self.assertEqual(params, {"type": "videos", "query": keyword, "sort": sort, "page": str(page), "limit": "32"})
                    self.assertEqual([v.video_id for v in result.videos], ["remote-first", "remote-second"])
                    self.assertEqual(result.current_page, page)
                    self.assertEqual(result.next_page, page + 1)
                    self.assertEqual(result.last_page, 2)
                    self.assertFalse(result.error)

    def test_translated_tag_names_are_canonicalized_without_becoming_keywords(self):
        self.api._get_json.return_value = {"results": [{"id": "tag-hit", "title": "Other title"}], "count": 1}
        for query in ("Hatsune Miku, 原神", "初音未来 原神", "#hatsune_miku，初音ミク；Genshin Impact"):
            with self.subTest(query=query):
                result = self.search(query, scope="tags", sort="likes")
                self.assertEqual([v.video_id for v in result.videos], ["tag-hit"])
                self.assertTrue(self.api._get_json.call_args.args[0].endswith("/videos"))
                self.assertEqual(self.api._get_json.call_args.kwargs["params"], {
                    "tags": "hatsune_miku,genshin_impact", "sort": "likes", "page": "0", "limit": "32",
                })
        self.search("原神")
        self.assertEqual(self.api._get_json.call_args.kwargs["params"]["query"], "原神")
        self.assertNotIn("tag", self.api._get_json.call_args.kwargs["params"])
        self.assertNotIn("tags", self.api._get_json.call_args.kwargs["params"])

    def test_unknown_tags_are_not_fuzzy_substituted(self):
        self.assertEqual(self.manager.tag_dictionary.resolve_query("hatsune_mik, custom_new_tag"), ("hatsune_mik", "custom_new_tag"))

    def test_separator_only_tags_do_not_silently_browse_unfiltered_videos(self):
        for query in (", ;", "#", "，；"):
            with self.subTest(query=query):
                self.assertTrue(self.search(query, scope="tags").error)
        self.api._get_json.assert_not_called()

    def test_ambiguous_translation_requests_explicit_tag_instead_of_first_alias(self):
        dictionary = self.manager.tag_dictionary
        cache = Path(dictionary.cache_path)
        payload = json.loads(cache.read_text(encoding="utf-8"))
        payload["ganshin"] = {"en": "Genshin", "zh-CN": "原神", "ja": "原神"}
        cache.write_text(json.dumps(payload), encoding="utf-8")
        dictionary.reload()
        result = self.search("原神", scope="tags")
        self.assertIn("genshin_impact", result.error)
        self.assertIn("ganshin", result.error)
        self.api._get_json.assert_not_called()
        result = self.search("genshin_impact, 初音未来", scope="tags")
        self.assertFalse(result.error)
        self.assertEqual(self.api._get_json.call_args.kwargs["params"]["tags"], "genshin_impact,hatsune_miku")

    def test_legacy_singular_tag_input_is_translated_for_page_and_bulk_callers(self):
        self.api.get_videos_page({"tag": "genshin_impact"})
        self.assertEqual(self.api._get_json.call_args.kwargs["params"]["tags"], "genshin_impact")
        self.assertNotIn("tag", self.api._get_json.call_args.kwargs["params"])
        self.api.get_videos_by_query({"tag": "genshin_impact"}, max_pages=1)
        self.assertEqual(self.api._get_json.call_args.kwargs["params"]["tags"], "genshin_impact")
        self.assertNotIn("tag", self.api._get_json.call_args.kwargs["params"])

    def test_blank_keywords_and_author_navigation_still_use_video_listing(self):
        for author_id in ("", "user-123"):
            with self.subTest(author_id=author_id):
                self.search("", sort="trending", author_id=author_id)
                self.assertTrue(self.api._get_json.call_args.args[0].endswith("/videos"))
                params = self.api._get_json.call_args.kwargs["params"]
                self.assertEqual(params.get("user", ""), author_id)
                self.assertEqual(params["sort"], "trending")
                self.assertNotIn("query", params)

    def test_unsupported_keyword_sort_does_not_reach_server(self):
        params = build_keyword_query_params(SearchFilters(keyword="dance", sort="trending"))
        self.assertEqual(params["sort"], "relevance")

    def test_error_and_malformed_payloads_are_not_reported_as_zero_matches(self):
        for payload in ({"message": "errors.invalidQuery"}, {"results": None}, {"results": ["bad-row"]}):
            with self.subTest(payload=payload):
                self.api._get_json.return_value = payload
                result = self.search("dance")
                self.assertTrue(result.error)
                self.assertFalse(result.has_more)
        self.api._get_json.side_effect = RuntimeError("HTTP 403: request forbidden")
        self.assertIn("HTTP 403", self.search("dance").error)
        self.api._get_json.side_effect = None
        self.api._get_json.return_value = {"results": [], "count": 0}
        result = self.search("no_matches")
        self.assertFalse(result.error)
        self.assertEqual(result.total, 0)

    def test_search_exact_count_is_not_a_listing_lower_bound(self):
        self.api._get_json.return_value = {"results": [{"id": str(i)} for i in range(32)], "count": 33}
        result = self.search("dance")
        self.assertEqual(result.total, 33)
        self.assertEqual(result.last_page, 1)
        self.assertTrue(result.has_more)

    def test_native_image_search_supports_four_orders_and_is_browse_only(self):
        self.api._get_json.return_value = {
            "results": [{
                "id": "img1", "title": "Miku~", "numViews": 5,
                "thumbnail": {"id": "file-1", "name": "file-1.png"},
                "user": {"username": "maker"},
            }],
            "count": 40,
        }
        for sort in ("relevance", "date", "views", "likes"):
            with self.subTest(sort=sort):
                result = self.search("miku", scope="images", sort=sort, page=1)
                self.assertTrue(self.api._get_json.call_args.args[0].endswith("/search"))
                self.assertEqual(self.api._get_json.call_args.kwargs["params"], {
                    "type": "images", "query": "miku", "sort": sort, "page": "1", "limit": "32",
                })
                image = result.videos[0]
                self.assertEqual(image.source_kind, "iwara_image")
                self.assertFalse(image.downloadable)
                self.assertEqual(image.source_url, "https://www.iwara.tv/image/img1")
                self.assertEqual(image.thumbnail_url, "https://i.iwara.tv/image/thumbnail/file-1/file-1.jpg")
                self.assertEqual(result.last_page, 1)

    def test_author_names_use_native_user_search_with_two_orders(self):
        self.api._get_json.return_value = {
            "results": [{"id": "u1", "username": "siu39", "name": "miku", "createdAt": "2026-09-26T14:23:45Z"}],
            "count": 164,
        }
        for sort, sent in (("relevance", "relevance"), ("date", "date"), ("views", "relevance")):
            with self.subTest(sort=sort):
                result = self.search("miku", scope="authors", sort=sort)
                self.assertEqual(self.api._get_json.call_args.kwargs["params"]["type"], "users")
                self.assertEqual(self.api._get_json.call_args.kwargs["params"]["sort"], sent)
                self.assertEqual([a.username for a in result.authors], ["siu39"])
                self.assertEqual(result.authors[0].joined_at[:10], "2026-09-26")
                self.assertEqual(result.last_page, 5)

    def test_exact_author_reference_still_reads_the_profile(self):
        self.api._get_json.return_value = {"user": {"id": "u1", "username": "creator"}}
        for keyword in ("@creator", "https://www.iwara.tv/profile/creator/videos"):
            with self.subTest(keyword=keyword):
                result = self.search(keyword, scope="authors")
                self.assertTrue(self.api._get_json.call_args.args[0].endswith("/profile/creator"))
                self.assertEqual([a.username for a in result.authors], ["creator"])

    def test_playlist_text_searches_playlists_and_references_list_videos(self):
        self.api._get_json.return_value = {
            "results": [{
                "id": "9612529c-ba2a-4d63-8eb2-89ef44adf0b2", "title": "miku", "numVideos": 27,
                "thumbnail": {"file": {"id": "f1"}, "thumbnail": 2},
                "user": {"username": "kan"},
            }],
            "count": 1,
        }
        result = self.search("miku", scope="playlists", sort="likes")
        params = self.api._get_json.call_args.kwargs["params"]
        self.assertEqual((params["type"], params["sort"]), ("playlists", "relevance"))
        playlist = result.playlists[0]
        self.assertEqual((playlist.title, playlist.video_count, playlist.author_username), ("miku", 27, "kan"))
        self.assertEqual(playlist.thumbnail_url, "https://i.iwara.tv/image/thumbnail/f1/thumbnail-02.jpg")
        self.assertEqual(playlist.source_url, "https://www.iwara.tv/playlist/9612529c-ba2a-4d63-8eb2-89ef44adf0b2")

        self.api._get_json.return_value = {"results": [{"id": "v1", "title": "Video"}]}
        for keyword in (playlist.source_url, playlist.playlist_id):
            with self.subTest(keyword=keyword):
                self.api._get_json.side_effect = [{"results": [{"id": "v1", "title": "Video"}]}, {"results": []}]
                result = self.search(keyword, scope="playlists")
                self.assertIn("/playlist/9612529c-ba2a-4d63-8eb2-89ef44adf0b2", self.api._get_json.call_args.args[0])
                self.assertEqual([v.video_id for v in result.videos], ["v1"])
                self.assertFalse(result.playlists)

    def test_server_page_size_is_used_for_next_page_without_inventing_last_page(self):
        self.api._get_json.return_value = {"results": [{"id": str(i)} for i in range(20)], "count": 45, "limit": 20}
        result = self.search("dance", page=1)
        self.assertTrue(result.has_more)
        self.assertIsNone(result.total)
        self.assertIsNone(result.last_page)
        self.assertEqual(result.next_page, 2)


class SearchModeInterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.context = ExitStack()
        self.addCleanup(self.context.close)
        root = self.context.enter_context(tempfile.TemporaryDirectory())
        settings = QSettings(os.path.join(root, "config.ini"), QSettings.Format.IniFormat)
        self.context.enter_context(patch.object(app_config, "_qs", settings))
        app_config.ui_language = "zh_CN"
        app_config.search_auto_search_enabled = False
        manager = Mock()
        manager.history.get_records.return_value = {}
        manager.get_search_tag_suggestions.return_value = []
        self.context.enter_context(patch("app.ui.search_page.download_manager", manager))
        self.context.enter_context(patch("app.ui.rules_page.RulePicker.refresh_rules"))
        self.page = SearchInterface()
        self.addCleanup(self.close_page)
        self.run_search = self.context.enter_context(patch.object(self.page, "_run_search"))
        self.page._set_combo_data(self.page._source_combo, "iwara")

    def close_page(self):
        self.page.shutdown(timeout_ms=1000)
        self.page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_keywords_tags_and_sources_keep_independent_text_and_sort(self):
        page = self.page
        page._keyword_edit.setText('"Blue dancer"')
        page._set_combo_data(page._sort_combo, "relevance")
        self.assertEqual([page._sort_combo.itemData(i) for i in range(page._sort_combo.count())], ["date", "relevance", "views", "likes"])
        page._set_combo_data(page._scope_combo, "tags")
        self.assertEqual(page._keyword_edit.text(), "")
        self.assertNotIn("relevance", [page._sort_combo.itemData(i) for i in range(page._sort_combo.count())])
        page._keyword_edit.setText("初音未来, 原神")
        page._set_combo_data(page._sort_combo, "likes")
        page._set_combo_data(page._scope_combo, "videos")
        self.assertEqual(page._keyword_edit.text(), '"Blue dancer"')
        self.assertEqual(page._sort_combo.currentData(), "relevance")
        page._set_combo_data(page._source_combo, "oreno3d")
        self.assertEqual(page._keyword_edit.text(), "")
        page._set_combo_data(page._source_combo, "iwara")
        self.assertEqual(page._keyword_edit.text(), '"Blue dancer"')
        page._set_combo_data(page._scope_combo, "tags")
        self.assertEqual(page._keyword_edit.text(), "初音未来, 原神")
        self.assertEqual(page._sort_combo.currentData(), "likes")

    def test_native_scopes_offer_only_orders_the_api_accepts(self):
        page = self.page
        sorts = lambda: [page._sort_combo.itemData(i) for i in range(page._sort_combo.count())]
        page._set_combo_data(page._scope_combo, "images")
        # Without a keyword images are browsed (/images), which also has trending/popularity.
        self.assertEqual(sorts(), ["date", "trending", "popularity", "views", "likes"])
        page._keyword_edit.setText("miku")
        self.assertEqual(sorts(), ["date", "relevance", "views", "likes"])
        page._keyword_edit.setText("")
        for scope in ("authors", "playlists"):
            with self.subTest(scope=scope):
                page._set_combo_data(page._scope_combo, scope)
                page._keyword_edit.setText("miku")
                self.assertEqual(sorts(), ["date", "relevance"])
        page._keyword_edit.setText("https://www.iwara.tv/playlist/9612529c-ba2a-4d63-8eb2-89ef44adf0b2")
        self.assertIn("trending", sorts())

    def test_loaded_covers_grow_with_the_grid(self):
        from PySide6.QtGui import QColor, QPixmap
        from app.core.search import SearchVideo

        path = os.path.join(tempfile.mkdtemp(), "cover.png")
        source = QPixmap(640, 360)
        source.fill(QColor("#336699"))
        source.save(path)
        page = self.page
        video = SearchVideo(video_id="v1", title="Cover")
        page._all_videos = [video]
        page._image_path_by_key["video:v1"] = path
        page.resize(1200, 900)
        page.show()
        QApplication.processEvents()
        _set_columns(page, 4)
        page._render_results()
        page._resize_grid()
        small = page._grid_icon_size
        _set_columns(page, 1)
        page._resize_grid()
        large = page._grid_icon_size
        self.assertGreater(large.width(), small.width())
        icon = page._item_by_key["video:v1"].icon()
        self.assertEqual(icon.availableSizes()[0], large)

    def test_grid_density_uses_the_shared_cover_size_slider(self):
        from app.ui.media_card import CardSizeControl

        page = self.page
        self.assertFalse(hasattr(page, "_grid_columns_combo"))
        self.assertIsInstance(page._card_size_control, CardSizeControl)
        page._card_min_width = 140
        small = page._grid_columns_for(1200, 12)
        page._card_min_width = 440
        large = page._grid_columns_for(1200, 12)
        self.assertGreater(small, large)
        self.assertEqual(page._grid_columns_for(100, 12), 1)  # never below one column

    def test_moving_the_slider_regrids_the_results(self):
        page = self.page
        page.resize(1200, 900)
        page.show()
        QApplication.processEvents()
        page._card_size_control.size_changed.emit(300)
        self.assertEqual(page._card_min_width, 300)
        wide = page._grid_item_size.width()
        page._card_size_control.size_changed.emit(140)
        self.assertLess(page._grid_item_size.width(), wide)

    def test_the_slider_is_only_offered_in_grid_view(self):
        page = self.page
        page.show()
        QApplication.processEvents()
        self.assertFalse(page._card_size_control.isHidden())
        page._set_combo_data(page._view_combo, "list")
        self.assertTrue(page._card_size_control.isHidden())

    def test_paging_keeps_query_and_sort_and_restarts_after_edits(self):
        self.page._keyword_edit.setText("dance")
        self.page._set_combo_data(self.page._sort_combo, "views")
        self.page._start_search()
        self.page._navigate_to_page(1)
        self.assertEqual(self.run_search.call_args.args[0].keyword, "dance")
        self.assertEqual(self.run_search.call_args.args[0].sort, "views")
        self.assertEqual(self.run_search.call_args.kwargs["page"], 1)
        self.page._set_combo_data(self.page._sort_combo, "likes")
        self.page._navigate_to_page(2)
        self.assertEqual(self.run_search.call_args.kwargs["page"], 0)
        self.assertEqual(self.run_search.call_args.args[0].sort, "likes")
        self.page._keyword_edit.setText("new keyword")
        self.page._navigate_to_page(2)
        self.assertEqual(self.run_search.call_args.kwargs["page"], 0)
        self.assertEqual(self.run_search.call_args.args[0].keyword, "new keyword")

    def test_history_restores_keyword_sort_after_restoring_text(self):
        with patch.object(self.page, "_start_search"):
            self.page._apply_search_history(json.dumps([{
                "source": "iwara", "scope": "videos", "keyword": "dance", "sort": "relevance",
            }]))
            self.app.processEvents()
        self.assertEqual(self.page._keyword_edit.text(), "dance")
        self.assertEqual(self.page._sort_combo.currentData(), "relevance")

    def test_sort_change_automatically_requests_first_page_with_new_order(self):
        app_config.search_auto_search_enabled = True
        self.page._keyword_edit.setText("dance")
        self.page._start_search()
        self.page._navigate_to_page(2)
        self.page._set_combo_data(self.page._sort_combo, "likes")
        QTest.qWait(180)
        self.assertEqual(self.run_search.call_args.args[0].keyword, "dance")
        self.assertEqual(self.run_search.call_args.args[0].sort, "likes")
        self.assertEqual(self.run_search.call_args.kwargs["page"], 0)

    def test_iwara_tag_completion_replaces_whole_multiword_label(self):
        self.page._set_combo_data(self.page._scope_combo, "tags")
        self.page._keyword_edit.setText("genshin_impact, Hatsune Mi")
        self.page._active_tag_edit = self.page._keyword_edit
        self.page._apply_tag_suggestion("hatsune_miku")
        self.assertEqual(self.page._keyword_edit.text(), "genshin_impact, hatsune_miku, ")
        self.page._set_combo_data(self.page._scope_combo, "videos")
        self.page._keyword_edit.setText("Hatsune Mi")
        self.page._apply_tag_suggestion("hatsune_miku")
        self.assertEqual(self.page._keyword_edit.text(), "Hatsune Mi")

    def test_context_change_invalidates_old_search_and_reset_clears_drafts(self):
        self.page._keyword_edit.setText("dance")
        self.page._start_search()
        generation = self.page._generation
        self.page._set_combo_data(self.page._scope_combo, "tags")
        self.assertGreater(self.page._generation, generation)
        self.assertIsNone(self.page._active_search_request)
        self.page._reset_filters()
        self.page._set_combo_data(self.page._source_combo, "iwara")
        self.assertEqual(self.page._keyword_edit.text(), "")

    def test_failure_status_survives_later_presentation_updates(self):
        self.page._update_status(SearchPageResult(scope="videos", error="HTTP 403"))
        self.page._update_status()
        self.assertIn("HTTP 403", self.page._status_label.text())
        self.assertNotIn("0 个视频", self.page._status_label.text())
        self.page._update_status(SearchPageResult(scope="videos", total=0))
        self.assertNotIn("HTTP 403", self.page._status_label.text())


if __name__ == "__main__":
    unittest.main()
