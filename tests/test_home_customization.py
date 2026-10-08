"""Home rows: editable layout, on-disk cache with change detection, loading and layout dialogs."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QPointF, Qt
from PySide6.QtGui import QEnterEvent
from PySide6.QtWidgets import QApplication, QWidget

from app.core import home_cache
from app.core.home_cache import HomeFeedCache, cache_key, rows_signature
from app.core.home_feed import (
    MODE_AUTHOR,
    MODE_BROWSE,
    MODE_KEYWORD,
    MODE_SUBSCRIPTIONS,
    MODE_TAGS,
    HomeSectionSpec,
    default_specs,
    home_sections,
    load_specs,
    sanitize_specs,
    save_specs,
)


class _Store:
    """Stands in for ``app_config``'s UI value store."""

    def __init__(self, value=""):
        self.value = value

    def get_ui_value(self, key, default=""):
        return self.value if key == "home_sections_v1" else default

    def set_ui_value(self, key, value, *, sync=True):
        self.value = value


def _rows(prefix: str, count: int = 3) -> list[dict]:
    return [{"id": f"{prefix}{i}", "title": f"{prefix} {i}", "user": {"username": "u"}} for i in range(count)]


class SpecTests(unittest.TestCase):
    def test_defaults_are_latest_feed_hot_videos_hot_images(self):
        self.assertEqual([s.id for s in default_specs()], ["latest", "subscriptions", "hot_videos", "hot_images"])
        self.assertEqual([s.id for s in home_sections(default_specs())], ["latest", "subscriptions", "hot_videos", "hot_images"])

    def test_missing_or_corrupt_storage_falls_back_to_defaults(self):
        for stored in ("", "not json", "{}", None):
            self.assertEqual([s.id for s in load_specs(_Store(stored))], ["latest", "subscriptions", "hot_videos", "hot_images"])

    def test_round_trip_keeps_order_and_enabled_flags(self):
        store = _Store()
        specs = [
            HomeSectionSpec("c-a", MODE_TAGS, "video", value="hmv", sort="date"),
            HomeSectionSpec("latest", MODE_BROWSE, "video", sort="date", enabled=False),
        ]
        save_specs(specs, store)
        loaded = load_specs(store)
        self.assertEqual([(s.id, s.enabled) for s in loaded], [("c-a", True), ("latest", False)])
        self.assertEqual([s.id for s in home_sections(loaded)], ["c-a"])  # disabled rows are skipped

    def test_an_empty_saved_list_means_an_empty_home(self):
        store = _Store("[]")
        self.assertEqual(load_specs(store), [])
        self.assertEqual(home_sections(load_specs(store)), ())

    def test_sanitising_drops_unusable_rows_and_repairs_the_rest(self):
        specs = sanitize_specs([
            {"id": "x", "mode": "keyword", "value": ""},  # nothing to search
            {"id": "y", "mode": "nope"},
            {"id": "z", "mode": "tags", "content": "image", "value": "hmv", "sort": "bogus"},
            {"id": "z", "mode": "author", "value": "@alice/", "sort": "likes"},  # duplicate id
            "junk",
        ])
        self.assertEqual(len(specs), 2)
        tags, author = specs
        self.assertEqual((tags.content, tags.sort), ("video", "date"))  # images have no tag filter
        self.assertEqual(author.value, "alice")
        self.assertNotEqual(tags.id, author.id)

    def test_tag_keyword_and_author_rows_become_single_tab_sections(self):
        sections = {s.id: s for s in home_sections([
            HomeSectionSpec("t", MODE_TAGS, "video", value="hmv", sort="date"),
            HomeSectionSpec("k", MODE_KEYWORD, "image", value="miku", sort="likes"),
            HomeSectionSpec("a", MODE_AUTHOR, "video", value="alice", sort="date"),
        ])}
        tags = sections["t"].tab("main")
        self.assertEqual(dict(tags.params), {"tags": "hmv", "sort": "date"})
        self.assertEqual(tags.search_request(), {"scope": "tags", "keyword": "hmv", "sort": "date"})
        keyword = sections["k"].tab("main")
        self.assertEqual(keyword.kind, "image")
        # /search ignores rating, so it is never sent for keyword rows
        self.assertNotIn("rating", keyword.request_params("nsfw"))
        self.assertEqual(keyword.search_request(), {"scope": "images", "keyword": "miku", "sort": "likes"})
        author = sections["a"].tab("main")
        self.assertEqual(author.request_params("nsfw"), {"sort": "date", "rating": "ecchi"})
        self.assertEqual(author.search_request(), {"author": ("alice", "alice", "", "")})
        self.assertEqual(sections["t"].title, "#hmv")
        self.assertEqual(sections["a"].title, "@alice")

    def test_a_browse_row_with_a_sort_has_no_tabs_and_without_one_has_three(self):
        fixed, tabs = home_sections([
            HomeSectionSpec("f", MODE_BROWSE, "video", sort="views"),
            HomeSectionSpec("g", MODE_BROWSE, "image"),
        ])
        self.assertEqual(len(fixed.tabs), 1)
        self.assertEqual([t.id for t in tabs.tabs], ["trending", "popularity", "date"])

    def test_subscription_row_needs_login(self):
        section = home_sections([HomeSectionSpec("subscriptions", MODE_SUBSCRIPTIONS)])[0]
        self.assertTrue(all(tab.needs_login for tab in section.tabs))
        self.assertFalse(section.tabs[0].opens_in_search)


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "cache.json")

    def test_put_reports_whether_the_posts_changed(self):
        cache = HomeFeedCache(self.path)
        self.assertTrue(cache.put("k", _rows("a")))
        self.assertFalse(cache.put("k", _rows("a")))  # same ids: nothing new
        self.assertTrue(cache.put("k", _rows("b")))
        self.assertEqual(rows_signature(cache.get("k").rows), ("b0", "b1", "b2"))

    def test_counters_changing_is_not_a_content_change(self):
        cache = HomeFeedCache(self.path)
        cache.put("k", [{"id": "a", "numLikes": 1}])
        self.assertFalse(cache.put("k", [{"id": "a", "numLikes": 9}]))
        self.assertEqual(cache.get("k").rows[0]["numLikes"], 9)

    def test_entries_survive_a_restart_and_age(self):
        HomeFeedCache(self.path).put("k", _rows("a"), now=1000.0)
        entry = HomeFeedCache(self.path).get("k")
        self.assertIsNotNone(entry)
        self.assertTrue(entry.is_fresh(600, now=1500.0))
        self.assertFalse(entry.is_fresh(600, now=1700.0))
        self.assertFalse(entry.is_fresh(0, now=1001.0))  # 0 never counts as fresh

    def test_touch_restarts_the_clock_without_changing_rows(self):
        cache = HomeFeedCache(self.path)
        cache.put("k", _rows("a"), now=10.0)
        cache.touch("k", now=500.0)
        entry = cache.get("k")
        self.assertEqual(entry.saved_at, 500.0)
        self.assertEqual(len(entry.rows), 3)

    def test_each_row_tab_and_rating_is_cached_separately(self):
        self.assertNotEqual(cache_key("hot", "trending", "all"), cache_key("hot", "date", "all"))
        self.assertNotEqual(cache_key("hot", "trending", "all"), cache_key("hot", "trending", "ecchi"))
        cache = HomeFeedCache(self.path)
        cache.put(cache_key("hot", "trending", "all"), _rows("a"))
        self.assertIsNone(cache.get(cache_key("hot", "trending", "ecchi")))

    def test_clearing_only_the_account_rows(self):
        cache = HomeFeedCache(self.path)
        cache.put(cache_key("subs", "videos", "all", account=True), _rows("s"))
        cache.put(cache_key("hot", "trending", "all"), _rows("h"))
        self.assertEqual(cache.clear(account_only=True), 1)
        self.assertEqual(len(cache), 1)
        self.assertEqual(cache.clear(), 1)
        self.assertEqual(len(cache), 0)

    def test_corrupt_file_is_ignored(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("{broken")
        cache = HomeFeedCache(self.path)
        self.assertIsNone(cache.get("k"))
        self.assertTrue(cache.put("k", _rows("a")))
        with open(self.path, encoding="utf-8") as handle:
            self.assertIn("k", json.load(handle))

    def test_old_entries_are_dropped_past_the_cap(self):
        with mock.patch.object(home_cache, "MAX_ENTRIES", 3):
            cache = HomeFeedCache(self.path)
            for index in range(5):
                cache.put(f"k{index}", _rows("a"), now=float(index + 1))
            self.assertEqual(sorted(cache._load()), ["k2", "k3", "k4"])


class _QtCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)


class SectionBlockCacheTests(_QtCase):
    """The row paints from cache and only asks the site when stale, changing cards only if posts changed."""

    def setUp(self):
        from app.ui import home_page
        from app.ui.home_workers import CoverFetcher, FeedWorker

        self.home_page = home_page
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = HomeFeedCache(os.path.join(self.tmp.name, "cache.json"))
        self.started: list = []
        for patcher in (
            mock.patch.object(FeedWorker, "start", lambda worker: self.started.append(worker)),
            mock.patch.object(home_page, "download_manager", Mock(is_logged_in=Mock(return_value=True))),
            mock.patch.object(home_page.app_config, "get_ui_value", side_effect=self._ui_value),
            mock.patch.object(home_page.app_config, "set_ui_value"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.minutes = 15
        self.fetcher = CoverFetcher()
        self.section = home_sections([HomeSectionSpec("hot", MODE_BROWSE, "video", sort="trending")])[0]
        self.block = home_page.SectionBlock(self.section, self.fetcher, cache=self.cache)
        self.block.resize(1200, 400)
        self.block.show()
        self.addCleanup(self._discard)

    def _ui_value(self, key, default=""):
        return self.minutes if key == "home_cache_minutes_v1" else default

    def _discard(self):
        self.block.shutdown(500)
        self.block.deleteLater()
        self.fetcher.shutdown(500)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def _result(self, rows):
        from app.ui.home_workers import FeedResult, items_from_rows

        return FeedResult(token=self.block._token, rows=rows, items=items_from_rows("video", rows))

    def test_empty_cache_loads_from_the_site_and_stores_the_result(self):
        self.block.reload()
        self.assertEqual(len(self.started), 1)
        self.assertTrue(self.block._grid.is_loading())  # skeleton while nothing is cached
        self.block._on_result(self._result(_rows("a")))
        self.assertEqual(len(self.block._grid.videos()), 3)
        self.assertFalse(self.block._grid.is_loading())
        self.assertEqual(len(self.cache.get(self.block.cache_key()).rows), 3)

    def test_fresh_cache_shows_instantly_without_any_request(self):
        self.cache.put(self.block.cache_key(), _rows("a"))
        self.block.reload()
        self.assertEqual(self.started, [])
        self.assertEqual(len(self.block._grid.videos()), 3)
        self.assertNotEqual(self.block._age.text(), "")

    def test_switching_back_and_forth_does_not_refetch_while_fresh(self):
        self.cache.put(self.block.cache_key(), _rows("a"))
        for _ in range(4):
            self.block.reload()
        self.assertEqual(self.started, [])

    def test_stale_cache_shows_first_then_checks_in_the_background(self):
        self.cache.put(self.block.cache_key(), _rows("a"), now=time.time() - 3600)
        self.block.reload()
        self.assertEqual(len(self.block._grid.videos()), 3)  # stale data is shown at once
        self.assertEqual(len(self.started), 1)
        self.assertFalse(self.block._grid.is_loading())  # no skeleton over real cards

    def test_unchanged_posts_leave_the_cards_alone(self):
        self.cache.put(self.block.cache_key(), _rows("a"), now=time.time() - 3600)
        self.block.reload()
        before = list(self.block._grid._cards)
        self.block._on_result(self._result(_rows("a")))
        self.assertEqual(self.block._grid._cards, before)  # same card objects: no rebuild
        self.assertTrue(self.cache.get(self.block.cache_key()).is_fresh(60))

    def test_changed_posts_replace_the_cards(self):
        self.cache.put(self.block.cache_key(), _rows("a"), now=time.time() - 3600)
        self.block.reload()
        self.block._on_result(self._result(_rows("b")))
        self.assertEqual([c.video.video_id for c in self.block._grid._cards], ["b0", "b1", "b2"])

    def test_new_posts_wait_while_cards_are_selected(self):
        self.cache.put(self.block.cache_key(), _rows("a"), now=time.time() - 3600)
        self.block.reload()
        self.block._grid._cards[0].set_selected(True, emit=True)
        self.block._on_result(self._result(_rows("b")))
        self.assertEqual(self.block._grid._cards[0].video.video_id, "a0")  # selection kept
        self.assertFalse(self.block._update_btn.isHidden())
        self.block._grid.clear_selection()  # releasing the selection applies the update
        self.assertEqual(self.block._grid._cards[0].video.video_id, "b0")
        self.assertTrue(self.block._update_btn.isHidden())

    def test_manual_refresh_always_asks_even_when_fresh(self):
        self.cache.put(self.block.cache_key(), _rows("a"))
        self.block.reload(force=True)
        self.assertEqual(len(self.started), 1)

    def test_a_failed_check_keeps_what_is_on_screen(self):
        from app.ui.home_workers import FeedResult

        self.cache.put(self.block.cache_key(), _rows("a"), now=time.time() - 3600)
        self.block.reload()
        self.block._on_result(FeedResult(token=self.block._token, error="HTTP 503"))
        self.assertEqual(len(self.block._grid.videos()), 3)
        self.assertEqual(self.block._age.toolTip(), "HTTP 503")

    def test_failed_first_load_offers_a_retry(self):
        from app.ui.home_workers import FeedResult

        self.block.reload()
        self.block._on_result(FeedResult(token=self.block._token, error="offline"))
        self.assertFalse(self.block._retry_btn.isHidden())
        self.assertIn("offline", self.block._state.text())

    def test_failed_checks_back_off_instead_of_retrying_every_tick(self):
        self.cache.put(self.block.cache_key(), _rows("a"), now=time.time() - 3600)
        self.block.reload()
        self.block.reload()
        self.block.reload()
        self.assertEqual(len(self.started), 1)

    def test_zero_minutes_means_only_manual_refresh(self):
        self.minutes = 0
        self.cache.put(self.block.cache_key(), _rows("a"), now=time.time() - 10 * 86400)
        self.block.reload()
        self.assertEqual(self.started, [])
        self.block.reload(force=True)
        self.assertEqual(len(self.started), 1)

    def test_each_tab_has_its_own_cache_entry(self):
        from app.ui.home_workers import CoverFetcher

        section = home_sections([HomeSectionSpec("hv", MODE_BROWSE, "video")])[0]
        block = self.home_page.SectionBlock(section, CoverFetcher(), cache=self.cache)
        self.addCleanup(block.deleteLater)
        first = block.cache_key()
        block._select_tab("date")
        self.assertNotEqual(first, block.cache_key())

    def test_a_stale_answer_for_an_older_request_is_ignored(self):
        self.block.reload(force=True)
        stale = self._result(_rows("a"))
        stale.token -= 1
        self.block._on_result(stale)
        self.assertEqual(self.block._grid.videos(), [])

    def test_folding_a_row_hides_its_cards_and_remembers_it(self):
        self.cache.put(self.block.cache_key(), _rows("a"))
        self.block.reload()
        self.block._on_fold_toggled(True)
        self.assertTrue(self.block._body.isHidden())
        self.block._on_fold_toggled(False)
        self.assertFalse(self.block._body.isHidden())


class FeedWorkerModeTests(unittest.TestCase):
    def _run(self, **kwargs):
        from app.ui import home_workers

        client = Mock()
        manager = Mock()
        manager.create_worker_api_client.return_value = client
        manager.get_home_page.return_value = ([{"id": "v1", "title": "t", "user": {}}], None, False, "")
        manager.tag_dictionary.resolve_query.return_value = ("hmv", "mmd")
        client.search_page.return_value = (
            [
                {"id": "g", "title": "safe", "rating": "general", "user": {}},
                {"id": "e", "title": "adult", "rating": "ecchi", "user": {}},
            ],
            None, False, "",
        )
        client.get_user_id.return_value = ("uid-9", "")
        worker = home_workers.FeedWorker(1, kwargs.pop("kind", "video"), kwargs.pop("params"), 0, 24, **kwargs)
        results = []
        worker.result_ready.connect(results.append)
        with mock.patch.object(home_workers, "download_manager", manager):
            worker.run()
        return results[0], manager, client

    def test_tag_rows_resolve_tags_through_the_dictionary(self):
        result, manager, _client = self._run(params={"tags": "hmv", "sort": "date"}, mode=MODE_TAGS, value="hmv")
        sent = manager.get_home_page.call_args.args[1]
        self.assertEqual(sent["tags"], "hmv,mmd")
        self.assertEqual(result.error, "")
        self.assertEqual(len(result.rows), 1)

    def test_keyword_rows_use_the_text_search_and_filter_rating_locally(self):
        result, manager, client = self._run(
            params={"query": "miku", "sort": "date"}, mode=MODE_KEYWORD, value="miku", rating="general",
        )
        self.assertEqual(client.search_page.call_args.args[0], "videos")
        manager.get_home_page.assert_not_called()
        self.assertEqual([v.video_id for v in result.items], ["g"])
        self.assertEqual(len(result.rows), 2)  # the cache keeps every row

    def test_author_rows_look_up_the_user_id_once(self):
        from app.ui import home_workers

        home_workers._AUTHOR_IDS.clear()
        result, manager, client = self._run(params={"sort": "date"}, mode=MODE_AUTHOR, value="Alice")
        self.assertEqual(manager.get_home_page.call_args.args[1]["user"], "uid-9")
        self._run(params={"sort": "date"}, mode=MODE_AUTHOR, value="alice")
        client.get_user_id.assert_called_once()  # remembered for the session

    def test_unknown_author_reports_an_error(self):
        from app.ui import home_workers

        home_workers._AUTHOR_IDS.clear()
        with mock.patch.object(home_workers, "download_manager") as manager:
            client = Mock()
            client.get_user_id.return_value = (None, "HTTP 404")
            manager.create_worker_api_client.return_value = client
            worker = home_workers.FeedWorker(1, "video", {"sort": "date"}, 0, 24, mode=MODE_AUTHOR, value="ghost")
            results = []
            worker.result_ready.connect(results.append)
            worker.run()
        self.assertEqual(results[0].error, "HTTP 404")


class LayoutDialogTests(_QtCase):
    def setUp(self):
        from app.ui.home_layout_dialog import HomeLayoutDialog

        self.host = QWidget()
        self.host.resize(900, 700)
        self.dialog = HomeLayoutDialog(self.host, specs=default_specs())
        self.addCleanup(self._close)

    def _close(self):
        self.dialog.deleteLater()
        self.host.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_lists_the_default_rows_in_order(self):
        self.assertEqual([s.id for s in self.dialog.specs()], ["latest", "subscriptions", "hot_videos", "hot_images"])

    def test_rows_can_be_hidden_moved_and_removed(self):
        dialog = self.dialog
        dialog._list.item(0).setCheckState(Qt.CheckState.Unchecked)
        dialog._list.setCurrentRow(3)
        dialog._move(-1)
        self.assertEqual([s.id for s in dialog.specs()], ["latest", "subscriptions", "hot_images", "hot_videos"])
        self.assertFalse(dialog.specs()[0].enabled)
        dialog._list.setCurrentRow(1)
        dialog._remove_current()
        self.assertEqual([s.id for s in dialog.specs()], ["latest", "hot_images", "hot_videos"])

    def test_saving_persists_and_announces_the_new_layout(self):
        from app.signal_bus import signal_bus

        store = _Store()
        announced = []

        def on_changed():
            announced.append(True)

        signal_bus.home_layout_changed.connect(on_changed)
        self.addCleanup(signal_bus.home_layout_changed.disconnect, on_changed)
        self.dialog._list.item(1).setCheckState(Qt.CheckState.Unchecked)
        with mock.patch("app.ui.home_layout_dialog.save_specs", side_effect=lambda specs: save_specs(specs, store)):
            self.assertTrue(self.dialog.validate())
        self.assertEqual(announced, [True])
        self.assertEqual([(s.id, s.enabled) for s in load_specs(store)][1], ("subscriptions", False))

    def test_editor_requires_a_value_for_search_rows(self):
        from app.ui.home_layout_dialog import SectionEditorDialog

        editor = SectionEditorDialog(None, self.host)
        self.addCleanup(editor.deleteLater)
        editor._mode.setCurrentIndex(editor._mode.findData(MODE_TAGS))
        self.assertFalse(editor.validate())
        editor._value.setText("hmv")
        self.assertTrue(editor.validate())
        spec = editor.result_spec
        self.assertEqual((spec.mode, spec.value, spec.content), (MODE_TAGS, "hmv", "video"))
        self.assertTrue(spec.id.startswith("c-"))

    def test_editing_a_builtin_into_something_else_gives_it_a_new_identity(self):
        from app.ui.home_layout_dialog import SectionEditorDialog

        original = default_specs()[2]  # hot_videos
        editor = SectionEditorDialog(original, self.host)
        self.addCleanup(editor.deleteLater)
        self.assertEqual(editor.collect().id, "hot_videos")  # untouched
        editor._mode.setCurrentIndex(editor._mode.findData(MODE_KEYWORD))
        editor._value.setText("blue")
        changed = editor.collect()
        self.assertNotEqual(changed.id, "hot_videos")
        self.assertEqual(changed.mode, MODE_KEYWORD)

    def test_sort_choices_follow_the_source(self):
        from app.ui.home_layout_dialog import SectionEditorDialog

        editor = SectionEditorDialog(None, self.host)
        self.addCleanup(editor.deleteLater)
        editor._mode.setCurrentIndex(editor._mode.findData(MODE_KEYWORD))
        keyword_sorts = [editor._sort.itemData(i) for i in range(editor._sort.count())]
        editor._mode.setCurrentIndex(editor._mode.findData(MODE_BROWSE))
        browse_sorts = [editor._sort.itemData(i) for i in range(editor._sort.count())]
        self.assertIn("relevance", keyword_sorts)
        self.assertNotIn("trending", keyword_sorts)
        self.assertEqual(browse_sorts[0], "")  # "show sort tabs"


class HomeInterfaceLayoutTests(_QtCase):
    def setUp(self):
        from app.ui import home_page

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.specs = default_specs()
        for target, value in (
            ("app.core.home_feed.load_specs", lambda store=None: list(self.specs)),
            ("app.core.home_cache._shared", HomeFeedCache(os.path.join(self.tmp.name, "cache.json"))),
            ("app.ui.home_workers.FeedWorker.run", lambda self: None),
            ("app.ui.media_detail.DetailWorker.run", lambda self: None),
        ):
            patcher = mock.patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.home = home_page.HomeInterface()
        self.addCleanup(self._discard)

    def _discard(self):
        self.home.shutdown(timeout_ms=1000)
        self.home.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_rows_follow_the_saved_layout_when_it_changes(self):
        from app.signal_bus import signal_bus

        self.assertEqual([b.section.id for b in self.home._feed.blocks], ["latest", "subscriptions", "hot_videos", "hot_images"])
        self.specs = [HomeSectionSpec("c-1", MODE_TAGS, "video", value="hmv", sort="date"), *self.specs[3:]]
        signal_bus.home_layout_changed.emit()
        self.assertEqual([b.section.id for b in self.home._feed.blocks], ["c-1", "hot_images"])
        self.assertTrue(self.home._feed._empty.isHidden())

    def test_an_empty_layout_shows_the_call_to_action(self):
        from app.signal_bus import signal_bus

        self.specs = []
        signal_bus.home_layout_changed.emit()
        self.assertEqual(self.home._feed.blocks, [])
        self.assertFalse(self.home._feed._empty.isHidden())

    def test_more_on_a_custom_row_opens_the_matching_search(self):
        from app.signal_bus import signal_bus

        self.specs = [
            HomeSectionSpec("c-t", MODE_TAGS, "video", value="hmv", sort="date"),
            HomeSectionSpec("c-a", MODE_AUTHOR, "video", value="alice", sort="date"),
        ]
        signal_bus.home_layout_changed.emit()
        searches, authors = [], []

        def on_search(request):
            searches.append(request)

        def on_author(target):
            authors.append(target)

        signal_bus.search_requested.connect(on_search)
        signal_bus.author_page_requested.connect(on_author)
        self.addCleanup(signal_bus.search_requested.disconnect, on_search)
        self.addCleanup(signal_bus.author_page_requested.disconnect, on_author)
        self.home._open_browse("c-t", "main")
        self.home._open_browse("c-a", "main")
        self.assertEqual(searches, [{"scope": "tags", "keyword": "hmv", "sort": "date"}])
        self.assertEqual(authors, [("alice", "alice", "", "")])

    def test_login_change_discards_account_rows_from_the_cache(self):
        cache = home_cache.shared_cache()
        cache.put(cache_key("subscriptions", "videos", "all", account=True), _rows("s"))
        cache.put(cache_key("hot_videos", "trending", "all"), _rows("h"))
        self.home._loaded_once = True
        self.home._on_login_changed(False)
        self.assertEqual(len(cache), 1)


class SkeletonAndCardTests(_QtCase):
    def test_placeholder_cards_show_until_real_ones_arrive(self):
        from app.core.search import SearchVideo
        from app.ui.media_card import MediaGrid

        grid = MediaGrid(selectable=False, max_rows=2)
        self.addCleanup(grid.deleteLater)
        grid.resize(1000, 300)
        grid.show()
        grid.set_loading(True)
        self.assertTrue(grid.is_loading())
        self.assertGreater(grid.height(), 100)  # reserves the space of two rows
        grid.set_videos([SearchVideo(video_id="a", title="A")])
        self.assertFalse(grid.is_loading())
        grid.set_loading(True)
        self.assertFalse(grid.is_loading())  # never over real cards
        grid.set_videos([])
        grid.set_loading(True)
        self.assertTrue(grid.is_loading())
        grid.set_loading(False)
        self.assertEqual(grid.height(), 0)

    def test_a_cover_arriving_fades_in_instead_of_popping(self):
        from PySide6.QtGui import QColor, QPixmap

        from app.core.search import SearchVideo
        from app.ui.media_card import MediaCard

        card = MediaCard(SearchVideo(video_id="a", title="A"))
        self.addCleanup(card.deleteLater)
        card.set_card_width(240)
        card.show()
        pixmap = QPixmap(64, 36)
        pixmap.fill(QColor("#336699"))
        card.set_cover(pixmap)
        self.assertEqual(card._cover_alpha, 0.0)
        deadline = time.time() + 2
        while card._cover_alpha < 1.0 and time.time() < deadline:
            self.app.processEvents()
        self.assertEqual(card._cover_alpha, 1.0)

    def test_hovering_animates_toward_one_and_back(self):
        from app.core.search import SearchVideo
        from app.ui.media_card import MediaCard

        card = MediaCard(SearchVideo(video_id="a", title="A"))
        self.addCleanup(card.deleteLater)
        card.set_card_width(240)
        card.show()
        point = QPointF(5, 5)
        card.enterEvent(QEnterEvent(point, point, point))
        deadline = time.time() + 2
        while card._hover_t < 1.0 and time.time() < deadline:
            self.app.processEvents()
        self.assertEqual(card._hover_t, 1.0)
        card.leaveEvent(QEvent(QEvent.Type.Leave))
        deadline = time.time() + 2
        while card._hover_t > 0.0 and time.time() < deadline:
            self.app.processEvents()
        self.assertEqual(card._hover_t, 0.0)


class SettingsHomeTests(_QtCase):
    def test_settings_have_a_home_category_with_both_cards(self):
        from app.ui.settings_sections import settings_categories

        home = next(c for c in settings_categories() if c[0] == "home")
        self.assertEqual(home[4], ("home_layout", "home_cache"))


if __name__ == "__main__":
    unittest.main()
