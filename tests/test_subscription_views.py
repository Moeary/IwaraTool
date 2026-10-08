"""Subscription overview/grid, shared card size, task speed, startup page and detail layout."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QSettings
from PySide6.QtWidgets import QApplication

from app.config import app_config
from app.core.download_runtime import format_speed, parse_speed
from app.core.manager import DownloadManager
from app.core.history import DownloadHistory
from app.core.models import DownloadTask, TaskStatus
from app.core.search import SearchVideo
from app.core.subscriptions import SubscriptionStore
from app.ui import subscription_views as views


def _item(video_id, **kwargs):
    data = {"video_id": video_id, "title": f"title {video_id}", "published_at": "2026-01-01T00:00:00Z"}
    data.update(kwargs)
    return data


class ItemHelperTests(unittest.TestCase):
    def test_state_priority(self):
        self.assertEqual(views.item_state(_item("a")), "")
        self.assertEqual(views.item_state(_item("a", is_new=1)), "new")
        self.assertEqual(views.item_state(_item("a", is_new=1, queued=True)), "queued")
        self.assertEqual(views.item_state(_item("a", downloaded=True, download_file_exists=True)), "downloaded")
        self.assertEqual(views.item_state(_item("a", downloaded=True, download_file_exists=False)), "moved")
        self.assertEqual(
            views.item_state(_item("a", is_new=1, downloaded=True, download_state="private")), "unavailable"
        )

    def test_filter_and_sort(self):
        items = [
            _item("a", title="Alpha", published_at="2026-01-03", is_new=1),
            _item("b", title="Beta", published_at="2026-01-02", downloaded=True, download_file_exists=True),
            _item("c", title="alpine", published_at="2026-01-01", queued=True),
        ]
        ids = lambda rows: [row["video_id"] for row in rows]  # noqa: E731
        self.assertEqual(ids(views.filter_items(items, "all", "al")), ["a", "c"])
        self.assertEqual(ids(views.filter_items(items, "ready")), ["a"])
        self.assertEqual(ids(views.filter_items(items, "downloaded")), ["b"])
        self.assertEqual(ids(views.filter_items(items, "queued")), ["c"])
        self.assertEqual(ids(views.sort_items(items, "date_desc")), ["a", "b", "c"])
        self.assertEqual(ids(views.sort_items(items, "date_asc")), ["c", "b", "a"])
        self.assertEqual(ids(views.sort_items(items, "title")), ["a", "c", "b"])
        self.assertEqual(ids(views.sort_items(list(reversed(items)), "new_first"))[0], "a")

    def test_downloadable_ids_skip_finished_queued_and_blocked(self):
        items = [
            _item("new", is_new=1),
            _item("old"),
            _item("done", downloaded=True, download_file_exists=True),
            _item("busy", queued=True),
            _item("blocked", download_state="private"),
            _item("new"),
        ]
        self.assertEqual(views.downloadable_ids(items), ["new", "old"])
        self.assertEqual(views.downloadable_ids(items, only_new=True), ["new"])

    def test_item_to_video_carries_state_and_a_fallback_link(self):
        video = views.item_to_video(_item("abc", is_new=1, author="creator"), author_name="Creator")
        self.assertEqual(video.raw["_state"], "new")
        self.assertEqual(video.author_name, "Creator")
        self.assertEqual(video.source_url, "https://www.iwara.tv/video/abc")

    def test_feed_sources_are_recognised(self):
        self.assertTrue(views.is_feed_source({"source_type": "feed"}))
        self.assertFalse(views.is_feed_source({"source_type": "author"}))


class SpeedTextTests(unittest.TestCase):
    def test_round_trip(self):
        self.assertEqual(parse_speed("1.5 MB/s"), 1.5 * 1024 ** 2)
        self.assertEqual(parse_speed("512 KB/s"), 512 * 1024)
        self.assertEqual(parse_speed("90 B/s"), 90)
        for junk in ("", "fast", "1 parsec/s", None):
            self.assertEqual(parse_speed(junk), 0.0)
        self.assertEqual(format_speed(parse_speed("2.0 MB/s")), "2.0 MB/s")
        self.assertEqual(format_speed(0), "0 B/s")


def _manager(directory: str) -> DownloadManager:
    manager = DownloadManager()
    manager.history = DownloadHistory(os.path.join(directory, "history.db"))
    manager.subscriptions = SubscriptionStore(os.path.join(directory, "subscriptions.db"))
    return manager


class RecentItemsTests(unittest.TestCase):
    def test_store_returns_only_the_newest_per_source(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = _manager(directory)
            one = manager.subscriptions.add_source("author", "one", "One")
            two = manager.subscriptions.add_source("author", "two", "Two")
            manager.subscriptions.upsert_items(
                one, [{"video_id": f"o{i}", "title": "t", "published_at": f"2026-01-0{i}T00:00:00Z"} for i in range(1, 6)]
            )
            manager.subscriptions.upsert_items(two, [{"video_id": "t1", "title": "t", "published_at": "2026-02-01"}])
            recent = manager.subscriptions.list_recent_items(3)
            self.assertEqual([i["video_id"] for i in recent[one]], ["o5", "o4", "o3"])
            self.assertEqual([i["video_id"] for i in recent[two]], ["t1"])

    def test_manager_adds_local_download_state(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = _manager(directory)
            source = manager.subscriptions.add_source("author", "one", "One")
            manager.subscriptions.upsert_items(source, [{"video_id": "v1", "title": "t", "published_at": "2026-01-01"}])
            with patch.object(manager.history, "get_records", return_value={"v1": {"file_path": "", "thumbnail_path": ""}}):
                item = manager.get_subscription_recent_items(4)[source][0]
            self.assertTrue(item["downloaded"])
            self.assertFalse(item["download_file_exists"])
            self.assertEqual(views.item_state(item), "moved")


class _QtCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)


class SubscriptionPageViewTests(_QtCase):
    def setUp(self):
        from app.ui.subscription_page import SubscriptionInterface

        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        settings = QSettings(os.path.join(self.directory.name, "config.ini"), QSettings.Format.IniFormat)
        patcher = patch.object(app_config, "_qs", settings)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.manager = _manager(self.directory.name)
        store = self.manager.subscriptions
        self.author = store.add_source("author", "creator", "Creator")
        self.playlist = store.add_source("playlist", "pl1", "Playlist 1")
        self.feed = store.add_source("feed", "subscribed", "Account Feed")
        store.upsert_items(
            self.author,
            [
                {"video_id": f"a{i}", "title": f"Author video {i}", "published_at": f"2026-03-{i:02d}T00:00:00Z", "author": "creator"}
                for i in range(1, 11)
            ],
        )
        store.upsert_items(self.feed, [{"video_id": "f1", "title": "Feed video", "published_at": "2026-03-01"}])
        for target in (
            "app.ui.subscription_views.CoverLoader.request",
            "app.ui.subscription_page.SubscriptionInterface._start_avatar_worker_for_missing_sources",
        ):
            started = patch(target)
            started.start()
            self.addCleanup(started.stop)
        manager_patch = patch("app.ui.subscription_page.download_manager", self.manager)
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

    def test_overview_is_the_default_and_hides_the_account_feed(self):
        self.assertIs(self.page._view_stack.currentWidget(), self.page._overview)
        self.assertEqual(set(self.page._overview._rows), {self.author, self.playlist})
        self.assertFalse(any(s["source_type"] == "feed" for s in self.page._all_sources))
        self.assertFalse(any(s["source_type"] == "feed" for s in self.page._sources))

    def test_row_strip_shows_the_latest_videos_first(self):
        strip = self.page._overview._rows[self.author].strip
        self.assertIsNotNone(strip)
        ids = [card.video.video_id for card in strip.cards]
        self.assertEqual(len(ids), views.RECENT_PER_SOURCE)
        self.assertEqual(ids[0], "a10")
        self.assertLessEqual(len(strip.shown_cards()), len(ids))
        self.assertIsNone(self.page._overview._rows[self.playlist].strip)

    def test_overview_filters_by_text_and_type(self):
        overview = self.page._overview
        overview._search.setText("playlist")
        overview.reload()
        self.assertEqual(set(overview._rows), {self.playlist})
        overview._search.setText("")
        overview._type_combo.setCurrentIndex(overview._type_combo.findData("author"))
        self.assertEqual(set(overview._rows), {self.author})

    def test_opening_a_row_shows_the_grid_and_back_returns(self):
        self.page._overview.source_opened.emit(self.author)
        self.app.processEvents()
        view = self.page._source_view
        self.assertIs(self.page._view_stack.currentWidget(), view)
        self.assertEqual(len(view._items), 10)
        self.assertEqual(len(view._grid.videos()), 10)
        view._search.setText("video 7")
        view._apply(reset_page=True)
        self.assertEqual([v.video_id for v in view._grid.videos()], ["a7"])
        view.back_requested.emit()
        self.assertIs(self.page._view_stack.currentWidget(), self.page._overview)

    def test_grid_pages_and_downloads_through_the_page(self):
        view = self.page._source_view
        view.open_source(next(s for s in self.page._all_sources if s["id"] == self.author))
        with patch.object(views, "GRID_PAGE_SIZE", 4):
            view._apply(reset_page=True)
            self.assertEqual(len(view._grid.videos()), 4)
            self.assertEqual(view._page_count(), 3)
            view._go(2)
            self.assertEqual(len(view._grid.videos()), 2)
        view._apply(reset_page=True)
        view._grid.select_all(True)
        with patch.object(self.page, "_enqueue_ids") as enqueue:
            view._download_selected()
            ids, = enqueue.call_args.args
            self.assertEqual(set(ids), {f"a{i}" for i in range(1, 11)})
            view._download_all()
            self.assertEqual(len(enqueue.call_args.args[0]), 10)

    def test_show_source_opens_the_grid_unless_the_table_is_active(self):
        self.page.show_source(self.author)
        self.assertIs(self.page._view_stack.currentWidget(), self.page._source_view)
        self.assertEqual(self.page._selected_source_id(), self.author)
        self.page._set_view_mode("table")
        self.assertIs(self.page._view_stack.currentWidget(), self.page._table_page)
        self.page.show_source(self.playlist)
        self.assertIs(self.page._view_stack.currentWidget(), self.page._table_page)
        self.page._set_view_mode("overview")
        self.assertIs(self.page._view_stack.currentWidget(), self.page._overview)
        self.assertEqual(app_config.get_ui_value("subscription_view_mode_v1"), "overview")

    def test_account_feed_button_is_gone(self):
        self.assertFalse(hasattr(self.page, "_add_following_feed"))

    def _quiet_author_page(self):
        """Keep the author page off the network and its status bar on the test manager."""

        for target, value in (
            ("app.ui.home_workers.FeedWorker.run", lambda worker: None),
            ("app.ui.home_workers.ApiCallWorker.run", lambda worker: None),
            ("app.ui.author_status.download_manager", self.manager),
        ):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_a_subscribed_author_opens_on_its_grid_with_the_status_bar(self):
        self._quiet_author_page()
        self.page.show_author(("creator", "Creator", "", ""))
        self.assertIs(self.page._view_stack.currentWidget(), self.page._source_view)
        self.assertFalse(self.page._source_view.status_bar.isHidden())
        self.assertEqual(self.page._source_view.status_bar.username, "creator")
        self.assertEqual(self.page._source_view.status_bar.source_id, self.author)
        self.page._open_source_grid(self.playlist)
        self.assertTrue(self.page._source_view.status_bar.isHidden())  # playlists have no author state

    def test_an_unknown_author_gets_the_live_author_page(self):
        self._quiet_author_page()
        self.page.show_author(("stranger", "Stranger", "u9", ""))
        self.assertIs(self.page._view_stack.currentWidget(), self.page._author_view)
        self.assertEqual(self.page._author_view.username, "stranger")
        self.assertEqual(self.page._author_view.status.source_id, 0)
        tab = self.page._author_view._browse._section.tab("videos")
        self.assertEqual((tab.mode, tab.value), ("author", "stranger"))

    def test_back_from_an_author_opened_here_returns_to_the_overview(self):
        self._quiet_author_page()
        returned = []
        self.page.return_requested.connect(lambda: returned.append(True))
        self.page.show_author(("stranger", "Stranger", "u9", ""))
        self.page._author_view.back_requested.emit()
        self.assertIs(self.page._view_stack.currentWidget(), self.page._overview)
        self.assertEqual(returned, [])

    def test_back_from_an_author_opened_by_another_page_leaves_this_page(self):
        self._quiet_author_page()
        returned = []
        self.page.return_requested.connect(lambda: returned.append(True))
        self.page.show_author(("stranger", "Stranger", "u9", ""), external=True)
        self.page._author_view.back_requested.emit()
        self.assertEqual(returned, [True])
        self.assertIs(self.page._view_stack.currentWidget(), self.page._overview)

    def test_removing_the_viewed_subscription_elsewhere_closes_its_grid(self):
        from app.signal_bus import signal_bus

        self._quiet_author_page()
        self.page.show_author(("creator", "Creator", "", ""))
        self.manager.subscriptions.remove_sources([self.author])
        signal_bus.subscription_sources_changed.emit()
        self.page._refresh_views()
        self.assertIs(self.page._view_stack.currentWidget(), self.page._overview)
        self.assertNotIn(self.author, self.page._overview._rows)

    def test_source_grid_asks_for_its_covers_urgently_and_reports_progress(self):
        view = self.page._source_view
        with patch("app.ui.subscription_views.CoverLoader.request") as request, \
                patch("app.ui.subscription_views.CoverLoader.pending", return_value=3):
            view.open_source(next(s for s in self.page._all_sources if s["id"] == self.author))
            view._sync_status()
        self.assertTrue(request.call_args.kwargs.get("urgent"))
        self.assertIn("10", view._status.text())

    def test_leaving_the_overview_drops_its_background_covers(self):
        with patch("app.ui.subscription_views.CoverLoader.drop_background") as drop:
            self.page._open_source_grid(self.author)
            self.app.processEvents()
        drop.assert_called()

    def test_card_activation_requests_the_detail_page(self):
        from app.signal_bus import signal_bus

        requested = []

        def collect(kind, item_id):
            requested.append((kind, item_id))

        signal_bus.media_detail_requested.connect(collect)
        self.addCleanup(signal_bus.media_detail_requested.disconnect, collect)
        self.page._overview._rows[self.author].strip.cards[0].activated.emit(
            self.page._overview._rows[self.author].strip.cards[0].video
        )
        self.assertEqual(requested, [("video", "a10")])


class CardSizeTests(_QtCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        settings = QSettings(os.path.join(self.directory.name, "config.ini"), QSettings.Format.IniFormat)
        patcher = patch.object(app_config, "_qs", settings)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_every_resizable_grid_follows_the_slider(self):
        from app.ui import media_card

        grid_a = media_card.MediaGrid(resizable=True)
        grid_b = media_card.MediaGrid(resizable=True, max_rows=2)
        fixed = media_card.MediaGrid(min_card_width=300)
        for widget in (grid_a, grid_b, fixed):
            self.addCleanup(widget.deleteLater)
        control = media_card.CardSizeControl()
        self.addCleanup(control.deleteLater)
        control._slider.setValue(360)
        control._apply()  # the slider applies after a short debounce
        self.assertEqual(grid_a._min_card_width, 360)
        self.assertEqual(grid_b._min_card_width, 360)
        self.assertEqual(fixed._min_card_width, 300)
        self.assertEqual(media_card.saved_card_width(), 360)
        self.assertEqual(media_card.MediaGrid(resizable=True)._min_card_width, 360)

    def test_width_is_clamped(self):
        from app.ui import media_card

        app_config.set_ui_value(media_card.CARD_WIDTH_KEY, 5000)
        self.assertEqual(media_card.saved_card_width(), media_card.CARD_WIDTH_MAX)
        app_config.set_ui_value(media_card.CARD_WIDTH_KEY, "oops")
        self.assertEqual(media_card.saved_card_width(), media_card.CARD_WIDTH_DEFAULT)

    def test_compact_cards_are_shorter_and_local_cards_hide_zero_stats(self):
        from app.ui.media_card import MediaCard

        video = SearchVideo("v", "title", raw={"_state": "new"})
        full = MediaCard(video)
        compact = MediaCard(video, compact=True)
        for card in (full, compact):
            self.addCleanup(card.deleteLater)
        self.assertLess(compact.height_for_width(240), full.height_for_width(240))
        self.assertIsNotNone(full.state_mark())
        self.assertIsNone(MediaCard(SearchVideo("v", "t")).state_mark())


class TaskSpeedTests(_QtCase):
    def test_total_speed_sums_only_downloading_tasks(self):
        from app.ui.task_page import TaskCenterInterface

        page = TaskCenterInterface()
        self.addCleanup(page.deleteLater)
        running_a = DownloadTask(task_id="a", video_id="a", title="A", url="u", status=TaskStatus.DOWNLOADING, speed_str="1.0 MB/s")
        running_b = DownloadTask(task_id="b", video_id="b", title="B", url="u", status=TaskStatus.DOWNLOADING, speed_str="512.0 KB/s")
        paused = DownloadTask(task_id="c", video_id="c", title="C", url="u", status=TaskStatus.COMPLETED, speed_str="9.0 MB/s")
        page._tasks_by_id = {task.task_id: task for task in (running_a, running_b, paused)}
        page._update_total_speed()
        self.assertEqual(page._total_speed_label.text(), "1.5 MB/s")
        page._tasks_by_id = {}
        page._update_total_speed()
        self.assertEqual(page._total_speed_label.text(), "0 B/s")


class StartupPageTests(_QtCase):
    def test_shortcut_numbers_follow_the_sidebar(self):
        from app.core import shortcut_defs

        defaults = {a.id: a.default for a in shortcut_defs.all_actions()}
        order = ["nav_home", "nav_subscriptions", "nav_search", "nav_download", "nav_repair", "nav_history", "nav_rules", "nav_settings"]
        self.assertEqual([defaults[a] for a in order], [f"Ctrl+{n}" for n in range(1, 9)])

    def test_setting_chooses_the_first_page(self):
        from types import SimpleNamespace

        from app.ui.main_window import MainWindow

        names = ("home", "subscription", "search", "download", "repair", "history", "rules", "settings")
        fake = SimpleNamespace(**{f"_{name}_page": object() for name in names})
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        settings = QSettings(os.path.join(directory.name, "config.ini"), QSettings.Format.IniFormat)
        with patch.object(app_config, "_qs", settings):
            self.assertEqual(app_config.startup_page, "home")
            self.assertIs(MainWindow._startup_page(fake), fake._home_page)
            for key, attr in (("search", "_search_page"), ("subscriptions", "_subscription_page"), ("settings", "_settings_page")):
                app_config.startup_page = key
                self.assertIs(MainWindow._startup_page(fake), getattr(fake, attr))
            app_config.startup_page = "no-such-page"
            self.assertIs(MainWindow._startup_page(fake), fake._home_page)


class DetailLayoutTests(_QtCase):
    def _view(self):
        from app.ui import media_detail
        from app.ui.home_workers import CoverFetcher

        view = media_detail.DetailView(CoverFetcher())
        self.addCleanup(view.deleteLater)
        return view

    def test_stage_follows_the_viewport_height_on_wide_windows(self):
        view = self._view()
        view.resize(1900, 1000)
        view.show()
        self.app.processEvents()
        stage = view._stage
        self.assertEqual(stage.height(), round(view._scroll.viewport().height() * 0.8))
        self.assertGreater(stage.width(), 1300)  # no longer capped at a narrow column

    def test_related_posts_sit_beside_the_post_only_when_wide(self):
        view = self._view()
        view.resize(1900, 1000)
        view.show()
        self.app.processEvents()
        self.assertTrue(view._side_mode)
        self.assertIs(view._related_box.parentWidget(), view._side)
        view.resize(900, 800)
        self.app.processEvents()
        self.assertFalse(view._side_mode)
        self.assertIsNot(view._related_box.parentWidget(), view._side)
        self.assertTrue(view._side.isHidden())


if __name__ == "__main__":
    unittest.main()
