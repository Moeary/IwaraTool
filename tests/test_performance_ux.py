"""Frame-drop fixes (incremental Home rebuild, card reuse, sliced lists, fewer disk writes) and keyboard UX."""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from unittest import mock
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from app.config import AppConfig, app_config
from app.core import shortcut_defs as defs
from app.core.home_cache import HomeFeedCache
from app.core.home_feed import MODE_TAGS, HomeSectionSpec, default_specs
from app.core.search import SearchVideo


class _QtCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def run(self, result=None):
        outcome = super().run(result)
        # Widgets scheduled with deleteLater() must really go before the next test:
        # a leaked page keeps listening on the shared signal bus and slows later suites.
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        return outcome

    def pump(self, until=lambda: False, seconds: float = 3.0):
        deadline = time.time() + seconds
        while not until() and time.time() < deadline:
            self.app.processEvents()
            time.sleep(0.002)
        self.app.processEvents()


def _videos(count: int, prefix: str = "v") -> list[SearchVideo]:
    return [SearchVideo(video_id=f"{prefix}{i}", title=f"title {i}") for i in range(count)]


class ConfigWriteTests(unittest.TestCase):
    """Re-applying a rule used to rewrite the settings file ~20 times per picker."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = QSettings(os.path.join(self.tmp.name, "config.ini"), QSettings.Format.IniFormat)
        self.syncs = 0
        # QSettings is a C++ type, so count its syncs through a delegating proxy.
        proxy_patch = mock.patch.object(app_config, "_qs", _SettingsProxy(self.settings, self._count))
        proxy_patch.start()
        self.addCleanup(proxy_patch.stop)

    def _count(self):
        self.syncs += 1

    def test_writing_the_same_value_again_does_not_touch_the_file(self):
        app_config.max_concurrent = 5
        before = self.syncs
        app_config.max_concurrent = 5
        self.assertEqual(self.syncs, before)
        app_config.set_ui_value("some_key", "x")
        after_first = self.syncs
        self.assertEqual(after_first, before + 1)
        app_config.set_ui_value("some_key", "x")
        self.assertEqual(self.syncs, after_first)

    def test_a_changed_value_is_written(self):
        app_config.max_concurrent = 5
        before = self.syncs
        app_config.max_concurrent = 6
        self.assertEqual(self.syncs, before + 1)
        self.assertEqual(app_config.max_concurrent, 6)

    def test_deferred_sync_writes_once_for_many_settings(self):
        before = self.syncs
        with app_config.deferred_sync():
            app_config.max_concurrent = 7
            app_config.filter_min_likes = 11
            app_config.download_thumbnail = True
            self.assertEqual(self.syncs, before)
        self.assertEqual(self.syncs, before + 1)
        self.assertEqual(app_config.filter_min_likes, 11)

    def test_deferred_sync_without_changes_does_not_write(self):
        app_config.max_concurrent = 3
        before = self.syncs
        with app_config.deferred_sync():
            app_config.max_concurrent = 3
        self.assertEqual(self.syncs, before)

    def test_applying_a_rule_is_one_write_and_idempotent(self):
        from app.core.rules import apply_rule_payload, default_rule_payload

        payload = default_rule_payload()
        payload["filter_min_likes"] = 123
        apply_rule_payload(payload)
        first = self.syncs
        apply_rule_payload(payload)
        self.assertEqual(self.syncs, first)  # same rule again: nothing to write

    def test_bool_and_int_comparisons_follow_the_stored_text(self):
        app_config.auto_login = False
        before = self.syncs
        app_config.auto_login = False
        self.assertEqual(self.syncs, before)
        app_config.auto_login = True
        self.assertEqual(self.syncs, before + 1)
        self.assertTrue(app_config.auto_login)


class _SettingsProxy:
    """Delegates to a real QSettings but reports every ``sync``."""

    def __init__(self, inner, on_sync):
        self._inner = inner
        self._on_sync = on_sync

    def sync(self):
        self._on_sync()
        return self._inner.sync()

    def __getattr__(self, name):
        return getattr(self._inner, name)


class MediaGridReuseTests(_QtCase):
    def _grid(self, **kwargs):
        from app.ui.media_card import MediaGrid

        grid = MediaGrid(selectable=True, **kwargs)
        self.addCleanup(grid.deleteLater)
        grid.resize(1000, 400)
        grid.show()
        self.app.processEvents()
        return grid

    def test_resizing_keeps_the_cards_that_still_fit(self):
        grid = self._grid(max_rows=2)
        videos = _videos(40)
        grid.set_videos(videos)
        before = list(grid._cards)
        self.assertGreater(len(before), 2)
        grid.resize(520, 400)
        self.app.processEvents()
        after = list(grid._cards)
        self.assertLess(len(after), len(before))
        self.assertEqual(after, before[: len(after)])  # same widgets, not rebuilt
        grid.resize(1000, 400)
        self.app.processEvents()
        again = list(grid._cards)
        self.assertEqual(again[: len(after)], after)

    def test_selection_survives_a_relayout(self):
        grid = self._grid(max_rows=2)
        grid.set_videos(_videos(40))
        grid._cards[0].set_selected(True, emit=True)
        grid.resize(700, 400)
        self.app.processEvents()
        self.assertEqual([v.video_id for v in grid.selected_videos()], ["v0"])

    def test_new_data_builds_new_cards(self):
        grid = self._grid(max_rows=2)
        grid.set_videos(_videos(10))
        before = set(map(id, grid._cards))
        grid.set_videos(_videos(10))  # equal ids, fresh objects: the data changed
        self.assertTrue(before.isdisjoint(set(map(id, grid._cards))))

    def test_hidden_grids_do_not_relayout_while_the_slider_moves(self):
        from app.ui.media_card import MediaGrid

        size = mock.patch("app.ui.media_card.saved_card_width", lambda: 200)
        size.start()
        self.addCleanup(size.stop)
        grid = MediaGrid(selectable=True, max_rows=2, resizable=True)
        self.addCleanup(grid.deleteLater)
        grid.resize(1000, 400)
        grid.show()
        grid.set_videos(_videos(40))
        self.app.processEvents()
        grid.hide()
        shown = len(grid._cards)
        with mock.patch.object(grid, "_rebuild") as rebuild:
            grid.set_min_card_width(400)
            rebuild.assert_not_called()
        self.assertTrue(grid._stale_layout)
        grid.show()
        self.app.processEvents()
        self.assertFalse(grid._stale_layout)
        self.assertLess(len(grid._cards), shown)  # caught up with the new size on show

    def test_a_cover_already_decoded_is_not_read_from_disk_again(self):
        from PySide6.QtGui import QColor, QPixmap

        grid = self._grid(max_rows=2)
        grid.set_videos(_videos(3))
        pixmap = QPixmap(8, 8)
        pixmap.fill(QColor("#123456"))
        grid.set_cover("v0", pixmap)
        with mock.patch("app.ui.media_card.QPixmap") as decode:
            grid._on_fetched_cover("video", "v0", "/does/not/matter.jpg")
            decode.assert_not_called()


class HomeIncrementalRebuildTests(_QtCase):
    def setUp(self):
        from app.ui import home_page

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.specs = default_specs()
        for target, value in (
            ("app.core.home_feed.load_specs", lambda store=None: list(self.specs)),
            ("app.core.home_cache._shared", HomeFeedCache(os.path.join(self.tmp.name, "c.json"))),
            ("app.ui.home_workers.FeedWorker.run", lambda worker: None),
            ("app.ui.media_detail.DetailWorker.run", lambda worker: None),
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

    def _ids(self):
        return [b.section.id for b in self.home._feed.blocks]

    def test_adding_a_row_keeps_the_existing_rows_as_they_are(self):
        from app.signal_bus import signal_bus

        before = {b.section.id: b for b in self.home._feed.blocks}
        self.specs = [*self.specs, HomeSectionSpec("c-new", MODE_TAGS, "video", value="hmv", sort="date")]
        signal_bus.home_layout_changed.emit()
        self.assertEqual(self._ids(), ["subscriptions", "hot_videos", "hot_images", "c-new"])
        for block in self.home._feed.blocks[:3]:
            self.assertIs(block, before[block.section.id])  # no rebuild of what did not change

    def test_removing_and_reordering_reuse_the_survivors(self):
        from app.signal_bus import signal_bus

        before = {b.section.id: b for b in self.home._feed.blocks}
        self.specs = [self.specs[2], self.specs[1]]  # hot_images, hot_videos
        signal_bus.home_layout_changed.emit()
        self.assertEqual(self._ids(), ["hot_images", "hot_videos"])
        for block in self.home._feed.blocks:
            self.assertIs(block, before[block.section.id])
        layout = self.home._feed._column
        positions = [layout.indexOf(b) for b in self.home._feed.blocks]
        self.assertEqual(positions, sorted(positions))

    def test_an_edited_row_is_rebuilt_but_its_neighbours_are_not(self):
        from app.signal_bus import signal_bus

        before = {b.section.id: b for b in self.home._feed.blocks}
        self.specs = [
            HomeSectionSpec("hot_videos", "browse", "video", sort="views"),  # same id, new definition
            self.specs[0],
        ]
        signal_bus.home_layout_changed.emit()
        self.assertEqual(self._ids(), ["hot_videos", "subscriptions"])
        blocks = {b.section.id: b for b in self.home._feed.blocks}
        self.assertIsNot(blocks["hot_videos"], before["hot_videos"])
        self.assertIs(blocks["subscriptions"], before["subscriptions"])

    def test_rebuilding_with_nothing_changed_creates_nothing(self):
        from app.signal_bus import signal_bus

        before = list(self.home._feed.blocks)
        signal_bus.home_layout_changed.emit()
        self.assertEqual(self.home._feed.blocks, before)


class SubscriptionOverviewTests(_QtCase):
    def setUp(self):
        from app.core.history import DownloadHistory
        from app.core.manager import DownloadManager
        from app.core.subscriptions import SubscriptionStore
        from app.ui.subscription_page import SubscriptionInterface

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        settings = QSettings(os.path.join(self.tmp.name, "config.ini"), QSettings.Format.IniFormat)
        patcher = mock.patch.object(app_config, "_qs", settings)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.manager = DownloadManager()
        self.manager.history = DownloadHistory(os.path.join(self.tmp.name, "history.db"))
        self.manager.subscriptions = SubscriptionStore(os.path.join(self.tmp.name, "subscriptions.db"))
        self.ids = []
        for index in range(14):
            source_id = self.manager.subscriptions.add_source("author", f"author{index}", f"Author {index:02d}")
            self.manager.subscriptions.upsert_items(source_id, [
                {"video_id": f"a{index}_{k}", "title": f"video {k}", "published_at": f"2026-03-{k + 1:02d}T00:00:00Z", "author": f"author{index}"}
                for k in range(5)
            ])
            self.ids.append(source_id)
        for target in (
            "app.ui.subscription_views.CoverLoader.request",
            "app.ui.subscription_page.SubscriptionInterface._start_avatar_worker_for_missing_sources",
        ):
            started = mock.patch(target)
            started.start()
            self.addCleanup(started.stop)
        manager_patch = mock.patch("app.ui.subscription_page.download_manager", self.manager)
        manager_patch.start()
        self.addCleanup(manager_patch.stop)
        self.page = SubscriptionInterface()
        self.page.resize(1300, 900)
        self.page.show()
        self.addCleanup(self._close)
        self.pump(lambda: not self.page._overview.building() and len(self.page._overview._rows) >= 4)

    def _close(self):
        self.page.shutdown(timeout_ms=1000)
        self.page.close()
        self.page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def _slot_positions(self, overview):
        order = sorted(overview._slots, key=lambda s: overview._order[s])
        return [overview._column.indexOf(overview._slots[s]) for s in order]

    def test_only_the_rows_near_the_viewport_are_real_widgets(self):
        overview = self.page._overview
        self.assertEqual(len(overview._slots), 14)  # every subscription has its place...
        self.assertGreater(len(overview._rows), 0)
        self.assertLess(len(overview._rows), 14)  # ...but not every place is a built row
        self.assertFalse(overview.building())
        self.assertEqual(self._slot_positions(overview), list(range(14)))
        built_ids = set(overview._rows)
        spacers = [sid for sid, widget in overview._slots.items() if sid not in built_ids]
        self.assertTrue(all(not isinstance(overview._slots[sid], type(next(iter(overview._rows.values())))) for sid in spacers))

    def test_scrolling_builds_the_far_rows_and_tears_down_the_first_ones(self):
        overview = self.page._overview
        first = min(overview._order, key=overview._order.get)
        last = max(overview._order, key=overview._order.get)
        self.assertIn(first, overview._rows)
        self.assertNotIn(last, overview._rows)
        bar = overview._scroll.verticalScrollBar()
        bar.setValue(bar.maximum())
        self.pump(lambda: last in overview._rows and not overview.building())
        self.assertIn(last, overview._rows)
        self.assertNotIn(first, overview._rows)  # far enough away to be torn down
        self.assertEqual(self._slot_positions(overview), list(range(14)))
        self.assertLess(len(overview._rows), 14)
        bar.setValue(0)
        self.pump(lambda: first in overview._rows and not overview.building())
        self.assertIn(first, overview._rows)

    def test_a_torn_down_row_keeps_its_height_so_the_scroll_bar_does_not_jump(self):
        overview = self.page._overview
        bar = overview._scroll.verticalScrollBar()
        before = bar.maximum()
        first = min(overview._order, key=overview._order.get)
        row_height = overview._rows[first].height()
        bar.setValue(bar.maximum())
        self.pump(lambda: first not in overview._rows)
        spacer = overview._slots[first]
        self.assertAlmostEqual(spacer.height(), row_height, delta=4)
        self.assertAlmostEqual(bar.maximum(), before, delta=200)  # far rows are estimates until built

    def test_slicing_yields_to_the_event_loop_between_batches(self):
        overview = self.page._overview
        for source_id in list(overview._rows):
            overview._tear_down_row(source_id)
        wanted = len(overview._wanted_ids(overview.BUILD_MARGIN))
        self.assertGreater(wanted, 1)
        with mock.patch.object(type(overview), "SLICE_SECONDS", 0.0), mock.patch.object(type(overview), "SYNC_ROWS", 0):
            overview._realize()
            self.assertTrue(overview.building())  # first row built, the rest waits for the next tick
            self.assertLess(len(overview._rows), wanted)
        self.pump(lambda: not overview.building())
        self.assertGreaterEqual(len(overview._rows), wanted)

    def test_reloading_unchanged_data_keeps_every_row(self):
        overview = self.page._overview
        before = dict(overview._rows)
        overview.reload()
        self.assertFalse(overview.building())
        self.assertEqual(overview._rows, before)
        for source_id, row in overview._rows.items():
            self.assertIs(row, before[source_id])

    def test_only_a_changed_source_gets_a_new_row(self):
        overview = self.page._overview
        before = dict(overview._rows)
        self.assertIn(self.ids[3], before)
        self.manager.subscriptions.upsert_items(self.ids[3], [
            {"video_id": "fresh", "title": "brand new", "published_at": "2026-04-01T00:00:00Z", "author": "author3"},
        ])
        self.page._load_sources()
        self.page._refresh_views()
        self.pump(lambda: not overview.building())
        changed = [sid for sid, row in overview._rows.items() if row is not before.get(sid)]
        self.assertEqual(changed, [self.ids[3]])

    def test_a_deleted_source_loses_its_place_and_the_rest_stay(self):
        overview = self.page._overview
        before = dict(overview._rows)
        self.manager.subscriptions.remove_sources([self.ids[0]])
        self.page._load_sources()
        self.page._refresh_views()
        self.pump(lambda: not overview.building())
        self.assertNotIn(self.ids[0], overview._rows)
        self.assertNotIn(self.ids[0], overview._slots)
        self.assertEqual(len(overview._slots), 13)
        for source_id, row in overview._rows.items():
            if source_id in before:
                self.assertIs(row, before[source_id])

    def test_sorting_reorders_the_places_and_reuses_surviving_rows(self):
        overview = self.page._overview
        before = dict(overview._rows)
        overview._toggle_direction()
        self.pump(lambda: not overview.building())
        self.assertEqual(self._slot_positions(overview), list(range(14)))
        for source_id, row in overview._rows.items():
            if source_id in before:
                self.assertIs(row, before[source_id])
        top = min(overview._order, key=overview._order.get)
        self.assertEqual(overview._order[top], 0)
        self.assertEqual(self.page._overview._slots[top].y(), min(w.y() for w in overview._slots.values()))

    def test_hidden_overview_defers_the_cover_size_change(self):
        overview = self.page._overview
        self.page._view_stack.setCurrentWidget(self.page._table_page)
        self.app.processEvents()
        with mock.patch.object(overview, "_apply_tile_size") as apply_size:
            overview._on_card_size(300)
            apply_size.assert_not_called()
        self.assertTrue(overview._tiles_stale)
        self.page._view_stack.setCurrentWidget(overview)
        self.app.processEvents()
        self.assertFalse(overview._tiles_stale)

    def test_recent_items_skip_the_per_video_disk_probe(self):
        with mock.patch.object(self.manager, "_ensure_subscription_thumbnail_cache") as probe:
            recent = self.manager.get_subscription_recent_items(5, resolve_thumbnails=False)
        probe.assert_not_called()
        self.assertTrue(all(item["thumbnail_path"] == "" for rows in recent.values() for item in rows))
        with mock.patch.object(self.manager, "_ensure_subscription_thumbnail_cache", return_value="") as probe:
            self.manager.get_subscription_recent_items(5)
        self.assertGreater(probe.call_count, 0)  # the default still resolves, for other callers

    def test_cover_path_is_resolved_on_demand_for_visible_covers(self):
        cover = os.path.join(self.tmp.name, "cover.jpg")
        with open(cover, "wb") as handle:
            handle.write(b"x")
        self.manager.history.upsert_downloaded({"video_id": "a0_0", "thumbnail_path": cover})
        self.assertEqual(self.manager.subscription_cover_path("a0_0", ""), cover)
        self.assertEqual(self.manager.subscription_cover_path("unknown", ""), "")
        self.assertEqual(self.manager.subscription_cover_path("", ""), "")

    def test_opening_the_page_builds_each_row_once(self):
        # The overview used to be built twice (once hidden, once on show).
        built = []
        original = type(self.page._overview)._make_row

        def counting(overview, source, *args, **kwargs):
            built.append(source["id"])
            return original(overview, source, *args, **kwargs)

        from app.ui.subscription_page import SubscriptionInterface

        with mock.patch.object(type(self.page._overview), "_make_row", counting):
            page = SubscriptionInterface()
            page.resize(1300, 900)
            page.show()
            self.pump(lambda: len(built) >= 3 and not page._overview.building())
            self.pump(seconds=0.3)
            page.shutdown(timeout_ms=1000)
            page.close()
            page.deleteLater()
        self.assertGreater(len(built), 0)
        self.assertEqual(len(built), len(set(built)))


class PixmapLRUTests(_QtCase):
    def _pixmap(self, side=100):
        from PySide6.QtGui import QColor, QPixmap

        pixmap = QPixmap(side, side)
        pixmap.fill(QColor("#336699"))
        return pixmap

    def test_evicts_the_least_recently_used_beyond_the_budget(self):
        from app.ui.media_card import PixmapLRU

        cache = PixmapLRU(max_bytes=3 * 100 * 100 * 4)
        for key in "abc":
            cache[key] = self._pixmap()
        self.assertEqual(len(cache), 3)
        cache.get("a")  # a becomes the most recent
        cache["d"] = self._pixmap()
        self.assertIn("a", cache)
        self.assertNotIn("b", cache)
        self.assertEqual(len(cache), 3)

    def test_replacing_a_key_does_not_double_count(self):
        from app.ui.media_card import PixmapLRU

        cache = PixmapLRU(max_bytes=2 * 100 * 100 * 4)
        for _ in range(5):
            cache["same"] = self._pixmap()
        cache["other"] = self._pixmap()
        self.assertEqual(len(cache), 2)

    def test_the_newest_item_stays_even_when_it_alone_is_over_budget(self):
        from app.ui.media_card import PixmapLRU

        cache = PixmapLRU(max_bytes=10)
        cache["big"] = self._pixmap(200)
        self.assertIn("big", cache)
        self.assertIsNotNone(cache.get("big"))
        self.assertIsNone(cache.get("missing"))
        with self.assertRaises(KeyError):
            cache["missing"]
        cache.clear()
        self.assertEqual(len(cache), 0)

    def test_a_grid_keeps_its_decoded_covers_inside_the_budget(self):
        from app.ui.media_card import MediaGrid

        grid = MediaGrid(selectable=False)
        self.addCleanup(grid.deleteLater)
        grid._covers = type(grid._covers)(max_bytes=4 * 100 * 100 * 4)
        for index in range(30):
            grid.set_cover(f"v{index}", self._pixmap())
        self.assertLessEqual(len(grid._covers), 4)


class HistoryLargeListTests(_QtCase):
    def _records(self, count):
        return [
            {"video_id": f"h{i}", "title": f"History {i}", "author": f"a{i % 7}", "file_path": "", "downloaded_at": f"2026-01-01 00:{i % 60:02d}:00"}
            for i in range(count)
        ]

    def _page(self, count):
        from app.ui.history_page import HistoryInterface

        patcher = mock.patch("app.ui.history_page.download_manager")
        manager = patcher.start()
        self.addCleanup(patcher.stop)
        manager.get_history_records.return_value = self._records(count)
        page = HistoryInterface()
        self.addCleanup(lambda: (page.close(), page.deleteLater()))
        page.resize(1200, 800)
        return page

    def test_small_histories_render_at_once(self):
        page = self._page(30)
        self.assertEqual(page._table.rowCount(), 30)
        self.assertIsNotNone(page._table.item(29, 1))

    def test_a_big_hidden_history_waits_until_it_is_shown(self):
        page = self._page(900)
        self.assertEqual(page._table.rowCount(), 900)
        self.assertIsNone(page._table.item(500, 1))  # nothing built while hidden
        self.assertTrue(page._render_deferred)
        page.show()
        self.pump(lambda: page._table.item(899, 1) is not None, seconds=8)
        self.assertIsNotNone(page._table.item(899, 1))
        self.assertEqual(page._table.item(0, 1).text(), page._visible_records[0]["title"])

    def test_a_big_visible_history_is_filled_in_slices(self):
        page = self._page(900)
        page.show()
        self.pump(lambda: page._table.item(899, 1) is not None, seconds=8)
        with mock.patch.object(type(page), "RENDER_SLICE_SECONDS", 0.0):
            page._apply_filters()
            self.assertLess(page._render_next, 900)  # yielded after one row
            self.assertTrue(page._render_timer.isActive())
        self.pump(lambda: page._render_next >= 900, seconds=10)
        self.assertEqual(page._render_next, 900)

    def test_a_new_filter_cancels_a_render_in_progress(self):
        page = self._page(900)
        page.show()
        self.pump(lambda: page._table.item(899, 1) is not None, seconds=8)
        with mock.patch.object(type(page), "RENDER_SLICE_SECONDS", 0.0):
            page._apply_filters()
        page._search_edit.setText("History 12")
        page._apply_filters()
        self.pump(lambda: page._render_next >= len(page._visible_records), seconds=5)
        self.assertEqual(page._table.rowCount(), len(page._visible_records))
        self.assertTrue(all("History 12" in r["title"] for r in page._visible_records))

    def test_typing_in_the_filter_is_debounced_for_big_histories(self):
        page = self._page(900)
        page.show()
        self.pump(lambda: page._table.item(899, 1) is not None, seconds=8)
        with mock.patch.object(page, "_apply_filters") as apply:
            page._filter_changed("H")
            page._filter_changed("Hi")
            apply.assert_not_called()
            self.assertTrue(page._filter_timer.isActive())

    def test_small_histories_filter_immediately(self):
        page = self._page(40)
        with mock.patch.object(page, "_apply_filters") as apply:
            page._filter_changed("H")
            apply.assert_called_once()

    def test_each_record_is_checked_on_disk_once_per_pass(self):
        page = self._page(60)
        with mock.patch("app.ui.history_page.os.path.isfile", return_value=False) as isfile:
            page._apply_filters()
        self.assertLessEqual(isfile.call_count, 60)  # was ~4 per record


class DetailScaleCacheTests(_QtCase):
    def test_the_stage_scales_a_cover_once_per_size(self):
        from PySide6.QtGui import QColor, QPixmap

        from app.ui.media_detail import MediaStage

        stage = MediaStage()
        self.addCleanup(stage.deleteLater)
        stage.resize(640, 360)
        pixmap = QPixmap(1280, 720)
        pixmap.fill(QColor("#224466"))
        stage.set_pixmap(pixmap)
        first = stage._scaled_pixmap()
        self.assertIs(stage._scaled_pixmap(), first)
        stage.resize(800, 450)
        self.assertIsNot(stage._scaled_pixmap(), first)

    def test_gallery_pictures_are_scaled_once_per_size_while_painting(self):
        from PySide6.QtGui import QColor, QPixmap

        from app.ui.media_detail import GalleryImage

        image = GalleryImage({"id": "f", "name": "f.png", "width": 400, "height": 300})
        self.addCleanup(image.deleteLater)
        image.resize(400, 300)
        pixmap = QPixmap(1600, 1200)
        pixmap.fill(QColor("#664422"))
        image.set_pixmap(pixmap, final=True)
        image.grab()
        first = image._scaled
        self.assertIsNotNone(first)
        image.grab()
        self.assertIs(image._scaled, first)


class ShortcutCatalogueTests(_QtCase):
    def test_new_actions_exist_with_valid_unique_keys(self):
        ids = {a.id for a in defs.all_actions()}
        for action_id in (
            "nav_next", "nav_prev", "focus_search", "quick_download", "open_download_folder", "rating_cycle",
            "card_size_up", "card_size_down", "card_size_reset", "toggle_theme", "toggle_fullscreen", "shortcut_help",
            "home_top", "home_bottom", "home_fold_all", "home_unfold_all", "detail_like", "detail_play",
            "detail_download", "detail_open_browser", "detail_copy_link", "detail_author_page", "nav_back",
            "search_prev_page", "search_next_page", "search_toggle_view", "search_toggle_controls", "search_reset",
            "sub_back", "sub_select_all", "sub_clear_selection", "sub_toggle_view", "sub_focus_filter",
        ):
            self.assertIn(action_id, ids)
            self.assertTrue(defs.normalize(defs.actions_by_id()[action_id].default), action_id)

    def test_grouped_shortcuts_put_global_first_then_the_current_page(self):
        from app.ui.shortcut_help import grouped_shortcuts

        groups = grouped_shortcuts(defs.SCOPE_SEARCH)
        titles = defs.scope_titles()
        self.assertEqual(groups[0][0], titles[defs.SCOPE_GLOBAL])
        self.assertEqual(groups[1][0], titles[defs.SCOPE_SEARCH])
        rows = dict(groups[0][1])
        self.assertEqual(rows[defs.actions_by_id()["shortcut_help"].label], "F1")

    def test_unassigned_actions_are_not_listed(self):
        from app.ui.shortcut_help import grouped_shortcuts

        with mock.patch.object(defs, "key_for", side_effect=lambda action_id: "" if action_id == "shortcut_help" else "Ctrl+X"):
            labels = [label for _title, rows in grouped_shortcuts() for label, _key in rows]
        self.assertNotIn(defs.actions_by_id()["shortcut_help"].label, labels)

    def test_the_help_dialog_builds_and_can_ask_for_the_settings_page(self):
        from app.ui.shortcut_help import ShortcutHelpDialog

        host = QWidget()
        host.resize(900, 700)
        self.addCleanup(host.deleteLater)
        dialog = ShortcutHelpDialog(host, defs.SCOPE_HOME)
        self.addCleanup(dialog.deleteLater)
        self.assertFalse(dialog.customize_requested)
        dialog.customize_btn.click()
        self.assertTrue(dialog.customize_requested)

    def test_hints_follow_the_users_binding(self):
        from app.ui.shortcuts import attach_hint

        button = QWidget()
        self.addCleanup(button.deleteLater)
        with mock.patch.object(defs, "key_for", return_value="Ctrl+Q"):
            attach_hint(button, "Do it", "home_refresh")
            self.assertEqual(button.toolTip(), "Do it (Ctrl+Q)")
        with mock.patch.object(defs, "key_for", return_value=""):
            from app.signal_bus import signal_bus

            signal_bus.shortcuts_changed.emit()
            self.assertEqual(button.toolTip(), "Do it")


class GlobalActionTests(_QtCase):
    """One window for the whole class: building a MainWindow costs about a second."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from app.ui.main_window import MainWindow

        cls.tmp = tempfile.TemporaryDirectory()
        settings = QSettings(os.path.join(cls.tmp.name, "config.ini"), QSettings.Format.IniFormat)
        cls.patchers = []
        for target, value in (
            ("app.config.app_config._qs", settings),
            ("app.ui.home_workers.FeedWorker.run", lambda worker: None),
            ("app.core.home_cache._shared", HomeFeedCache(os.path.join(cls.tmp.name, "cache.json"))),
        ):
            patcher = mock.patch(target, value)
            patcher.start()
            cls.patchers.append(patcher)
        cls.previous = MainWindow._window_ref
        cls.window = MainWindow()

    @classmethod
    def tearDownClass(cls):
        from app.ui.main_window import MainWindow

        cls.window._reloading_language = True
        cls.window.close()
        cls.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        MainWindow._window_ref = cls.previous
        for patcher in cls.patchers:
            patcher.stop()
        cls.tmp.cleanup()

    def setUp(self):
        self.window.switchTo(self.window._home_page)

    def _fire(self, action_id, parent=None):
        from app.ui.shortcuts import registry

        parent = parent or self.window
        shortcut = [s for s in registry.live(action_id) if s.parent() is parent][0]
        shortcut.activated.emit()

    def test_next_and_previous_page_cycle_through_the_sidebar(self):
        window = self.window
        window.switchTo(window._home_page)
        self._fire("nav_next")
        self.assertIs(window.stackedWidget.currentWidget(), window._subscription_page)
        self._fire("nav_prev")
        self.assertIs(window.stackedWidget.currentWidget(), window._home_page)
        self._fire("nav_prev")  # wraps to the last page
        self.assertIs(window.stackedWidget.currentWidget(), window._settings_page)
        self._fire("nav_next")
        self.assertIs(window.stackedWidget.currentWidget(), window._home_page)

    def test_quick_search_goes_to_the_search_box(self):
        window = self.window
        with mock.patch.object(window._search_page, "_focus_keyword") as focus:
            self._fire("focus_search")
        self.assertIs(window.stackedWidget.currentWidget(), window._search_page)
        focus.assert_called_once()

    def test_rating_cycles_and_is_broadcast(self):
        from app.signal_bus import signal_bus

        seen = []

        def record(value):
            seen.append(value)

        signal_bus.content_rating_changed.connect(record)
        self.addCleanup(signal_bus.content_rating_changed.disconnect, record)
        with mock.patch("app.ui.main_window.InfoBar.info"):
            for _ in range(3):
                self._fire("rating_cycle")
        self.assertEqual(len(seen), 3)
        self.assertEqual(len(set(seen)), 3)  # all -> general -> ecchi -> all, whatever the start

    def test_cover_size_steps_and_resets_and_stays_in_range(self):
        from app.signal_bus import signal_bus
        from app.ui import media_card

        seen = []

        def record(value):
            seen.append(value)

        signal_bus.media_card_size_changed.connect(record)
        self.addCleanup(signal_bus.media_card_size_changed.disconnect, record)
        self._fire("card_size_reset")
        self.assertEqual(seen[-1], media_card.CARD_WIDTH_DEFAULT)
        self._fire("card_size_up")
        self.assertEqual(seen[-1], media_card.CARD_WIDTH_DEFAULT + media_card.CARD_WIDTH_STEP)
        self._fire("card_size_down")
        self._fire("card_size_down")
        self.assertEqual(seen[-1], media_card.CARD_WIDTH_DEFAULT - media_card.CARD_WIDTH_STEP)
        for _ in range(40):
            media_card.step_card_width(-1)
        self.assertEqual(media_card.saved_card_width(), media_card.CARD_WIDTH_MIN)
        for _ in range(40):
            media_card.step_card_width(1)
        self.assertEqual(media_card.saved_card_width(), media_card.CARD_WIDTH_MAX)

    def test_toggle_theme_and_fullscreen_are_wired(self):
        with mock.patch.object(self.window, "_toggle_dark_mode") as toggle:
            from app.ui.shortcuts import registry

            [s for s in registry.live("toggle_theme") if s.parent() is self.window][0].activated.emit()
        toggle.assert_called_once()
        with mock.patch.object(self.window, "showFullScreen") as full, mock.patch.object(self.window, "showNormal") as normal:
            self._fire("toggle_fullscreen")
            full.assert_called_once()
            with mock.patch.object(self.window, "isFullScreen", return_value=True):
                self._fire("toggle_fullscreen")
            normal.assert_called_once()

    def test_quick_download_pastes_and_submits_on_the_download_page(self):
        window = self.window
        with mock.patch.object(window._download_page, "_paste_from_clipboard") as paste:
            self._fire("quick_download")
        self.assertIs(window.stackedWidget.currentWidget(), window._download_page)
        paste.assert_called_once_with(submit=True)

    def test_open_download_folder_only_when_it_exists(self):
        window = self.window
        with mock.patch("app.ui.main_window.QDesktopServices.openUrl") as open_url, \
                mock.patch.object(window._home_page.__class__, "isVisible", return_value=True):
            with mock.patch.object(app_config.__class__, "download_dir", self.tmp.name, create=True):
                window.open_download_folder()
            open_url.assert_called_once()
        with mock.patch("app.ui.main_window.QDesktopServices.openUrl") as open_url, \
                mock.patch("app.ui.main_window.InfoBar.warning") as warn:
            with mock.patch.object(app_config.__class__, "download_dir", os.path.join(self.tmp.name, "missing"), create=True):
                window.open_download_folder()
            open_url.assert_not_called()
            warn.assert_called_once()

    def test_f1_opens_the_cheat_sheet_and_can_jump_to_settings(self):
        window = self.window
        with mock.patch("app.ui.shortcut_help.ShortcutHelpDialog.exec") as run:
            self._fire("shortcut_help")
        run.assert_called_once()

        def accept_with_customize(dialog):
            dialog.customize_requested = True
            return 1

        with mock.patch("app.ui.shortcut_help.ShortcutHelpDialog.exec", accept_with_customize):
            window.show_shortcut_help()
        self.assertIs(window.stackedWidget.currentWidget(), window._settings_page)
        self.assertEqual(window._settings_page._settings_board.current_category(), "shortcuts")


class HomeKeyboardActionTests(_QtCase):
    def setUp(self):
        from app.ui import home_page

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for target, value in (
            ("app.core.home_feed.load_specs", lambda store=None: default_specs()),
            ("app.core.home_cache._shared", HomeFeedCache(os.path.join(self.tmp.name, "c.json"))),
            ("app.ui.home_workers.FeedWorker.run", lambda worker: None),
            ("app.ui.media_detail.DetailWorker.run", lambda worker: None),
        ):
            patcher = mock.patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.home = home_page.HomeInterface()
        self.home.resize(1200, 800)
        self.home.show()
        self.addCleanup(self._discard)

    def _discard(self):
        self.home.shutdown(timeout_ms=1000)
        self.home.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_fold_and_unfold_every_row(self):
        self.home.fold_all(True)
        self.assertTrue(all(block._collapsed for block in self.home._feed.blocks))
        self.assertTrue(all(block._body.isHidden() for block in self.home._feed.blocks))
        self.home.fold_all(False)
        self.assertFalse(any(block._collapsed for block in self.home._feed.blocks))

    def test_post_actions_only_run_while_a_post_is_open(self):
        with mock.patch.object(self.home._detail, "_toggle_like") as like:
            self.home.detail_action("like")
            like.assert_not_called()  # on the feed
            self.home.show_detail("video", "abc")
            self.home.detail_action("like")
            like.assert_called_once()
        with mock.patch.object(self.home._detail, "_play") as play, mock.patch.object(self.home._detail, "_queue") as queue, \
                mock.patch.object(self.home._detail, "_copy_link") as copy, mock.patch.object(self.home._detail, "_open_in_browser") as browser, \
                mock.patch.object(self.home._detail, "_author_page") as author:
            for name in ("play", "download", "copy", "browser", "author", "nonsense"):
                self.home.detail_action(name)
        for mocked in (play, queue, copy, browser, author):
            mocked.assert_called_once()

    def test_escape_closes_a_post_but_does_nothing_on_the_feed(self):
        self.home.close_current()
        self.assertEqual(self.home._stack.currentIndex(), self.home._FEED)
        self.home.show_detail("video", "abc")
        self.home.close_current()
        self.assertEqual(self.home._stack.currentIndex(), self.home._FEED)

    def test_scrolling_to_the_ends(self):
        bar = self.home._feed._scroll.verticalScrollBar()
        self.home.scroll_to(bottom=True)
        self.assertEqual(bar.value(), bar.maximum())
        self.home.scroll_to(bottom=False)
        self.assertEqual(bar.value(), bar.minimum())


class SubscriptionKeyboardActionTests(_QtCase):
    def setUp(self):
        from app.core.history import DownloadHistory
        from app.core.manager import DownloadManager
        from app.core.subscriptions import SubscriptionStore
        from app.ui.subscription_page import SubscriptionInterface

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        settings = QSettings(os.path.join(self.tmp.name, "config.ini"), QSettings.Format.IniFormat)
        patcher = mock.patch.object(app_config, "_qs", settings)
        patcher.start()
        self.addCleanup(patcher.stop)
        manager = DownloadManager()
        manager.history = DownloadHistory(os.path.join(self.tmp.name, "history.db"))
        manager.subscriptions = SubscriptionStore(os.path.join(self.tmp.name, "subscriptions.db"))
        self.source = manager.subscriptions.add_source("author", "creator", "Creator")
        manager.subscriptions.upsert_items(self.source, [
            {"video_id": f"a{i}", "title": f"video {i}", "published_at": f"2026-03-{i + 1:02d}T00:00:00Z", "author": "creator"}
            for i in range(6)
        ])
        for target in (
            "app.ui.subscription_views.CoverLoader.request",
            "app.ui.subscription_page.SubscriptionInterface._start_avatar_worker_for_missing_sources",
        ):
            started = mock.patch(target)
            started.start()
            self.addCleanup(started.stop)
        manager_patch = mock.patch("app.ui.subscription_page.download_manager", manager)
        manager_patch.start()
        self.addCleanup(manager_patch.stop)
        self.page = SubscriptionInterface()
        self.page.resize(1300, 900)
        self.page.show()
        self.app.processEvents()
        self.addCleanup(self._close)

    def _close(self):
        self.page.shutdown(timeout_ms=1000)
        self.page.close()
        self.page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_back_returns_from_an_opened_subscription(self):
        self.page._open_source_grid(self.source)
        self.assertIs(self.page._view_stack.currentWidget(), self.page._source_view)
        self.page.go_back_view()
        self.assertIs(self.page._view_stack.currentWidget(), self.page._overview)
        self.page.go_back_view()  # already on the list: nothing happens
        self.assertIs(self.page._view_stack.currentWidget(), self.page._overview)

    def test_select_all_and_clear_work_on_the_grid(self):
        self.page._open_source_grid(self.source)
        self.page.select_all_in_view()
        self.assertEqual(len(self.page._source_view._grid.selected_videos()), 6)
        self.page.clear_selection_in_view()
        self.assertEqual(self.page._source_view._grid.selected_videos(), [])

    def test_toggle_between_overview_and_table(self):
        self.page.toggle_view_mode()
        self.assertIs(self.page._view_stack.currentWidget(), self.page._table_page)
        self.page.toggle_view_mode()
        self.assertIs(self.page._view_stack.currentWidget(), self.page._overview)

    def test_focus_filter_picks_the_visible_views_box(self):
        self.page.focus_filter()
        self.assertTrue(self.page._overview._search.hasFocus() or self.page._overview._search.lineEdit().hasFocus())
        self.page._open_source_grid(self.source)
        self.page.focus_filter()
        self.assertTrue(self.page._source_view._search.hasFocus() or self.page._source_view._search.lineEdit().hasFocus())


class GridKeyboardTests(_QtCase):
    def setUp(self):
        from app.ui.media_card import MediaGrid

        self.host = QWidget()
        self.host.resize(1000, 700)
        self.grid = MediaGrid(self.host, selectable=True, min_card_width=200)
        self.grid.resize(1000, 600)
        self.host.show()
        self.grid.set_videos(_videos(10))
        self.app.processEvents()
        self.grid.setFocus()
        self.app.processEvents()
        self.activated = []
        self.grid.card_activated.connect(lambda video: self.activated.append(video.video_id))
        self.addCleanup(self.host.deleteLater)

    def key(self, key, modifiers=Qt.KeyboardModifier.NoModifier):
        QTest.keyClick(self.grid, key, modifiers)
        self.app.processEvents()

    def test_the_cursor_starts_on_the_first_card_and_moves_with_the_arrows(self):
        columns = self.grid._columns
        self.assertGreaterEqual(columns, 3)
        self.assertEqual(self.grid.cursor_index(), 0)
        self.key(Qt.Key.Key_Right)
        self.assertEqual(self.grid.cursor_index(), 1)
        self.key(Qt.Key.Key_Down)
        self.assertEqual(self.grid.cursor_index(), 1 + columns)
        self.key(Qt.Key.Key_Up)
        self.key(Qt.Key.Key_Left)
        self.assertEqual(self.grid.cursor_index(), 0)
        self.key(Qt.Key.Key_Left)  # no wrap past the start
        self.assertEqual(self.grid.cursor_index(), 0)
        self.key(Qt.Key.Key_End)
        self.assertEqual(self.grid.cursor_index(), 9)
        self.key(Qt.Key.Key_Right)
        self.assertEqual(self.grid.cursor_index(), 9)
        self.key(Qt.Key.Key_Home)
        self.assertEqual(self.grid.cursor_index(), 0)

    def test_only_the_cursor_card_shows_the_focus_ring(self):
        self.key(Qt.Key.Key_Right)
        flags = [card._current for card in self.grid._cards]
        self.assertEqual(flags.count(True), 1)
        self.assertTrue(flags[1])

    def test_enter_opens_and_space_ticks(self):
        self.key(Qt.Key.Key_Right)
        self.key(Qt.Key.Key_Return)
        self.assertEqual(self.activated, ["v1"])
        self.key(Qt.Key.Key_Space)
        self.assertEqual([v.video_id for v in self.grid.selected_videos()], ["v1"])
        self.key(Qt.Key.Key_Space)
        self.assertEqual(self.grid.selected_videos(), [])

    def test_ctrl_a_selects_everything_and_escape_clears(self):
        self.key(Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(len(self.grid.selected_videos()), 10)
        self.key(Qt.Key.Key_Escape)
        self.assertEqual(self.grid.selected_videos(), [])

    def test_the_menu_key_requests_the_context_menu(self):
        requested = []
        self.grid.card_context_requested.connect(lambda video, pos: requested.append(video.video_id))
        self.key(Qt.Key.Key_Menu)
        self.assertEqual(requested, ["v0"])

    def test_escape_and_ctrl_a_are_claimed_over_page_shortcuts(self):
        from PySide6.QtGui import QKeyEvent

        self.grid.select_all(True)
        event = QKeyEvent(QEvent.Type.ShortcutOverride, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
        event.ignore()
        self.grid.event(event)
        self.assertTrue(event.isAccepted())
        self.grid.clear_selection()
        event = QKeyEvent(QEvent.Type.ShortcutOverride, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
        event.ignore()
        self.grid.event(event)
        self.assertFalse(event.isAccepted())  # nothing selected: Esc stays free for the page

    def test_clicking_a_card_gives_the_grid_the_keyboard(self):
        other = QWidget(self.host)
        other.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        other.show()
        other.setFocus()
        self.app.processEvents()
        card = self.grid._cards[2]
        QTest.mouseClick(card, Qt.MouseButton.LeftButton, pos=card.rect().center())
        self.app.processEvents()
        self.assertTrue(self.grid.hasFocus())

    def test_the_cursor_is_kept_inside_the_list_when_it_shrinks(self):
        self.key(Qt.Key.Key_End)
        self.grid.set_videos(_videos(3, "n"))
        self.assertLessEqual(self.grid.cursor_index(), 2)
        self.grid.set_videos([])
        self.key(Qt.Key.Key_Right)  # an empty grid ignores keys instead of crashing
        self.assertEqual(self.grid.cursor_index(), -1)


if __name__ == "__main__":
    unittest.main()
