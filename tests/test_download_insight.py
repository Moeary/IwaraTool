"""Link preview, "why not downloaded", watched history, the history browse views and scroll fixes."""

import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, QSize, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QListWidgetItem

from app.config import app_config
from app.core.history import DownloadHistory
from app.core.manager import DownloadManager
from app.core.models import DownloadTask, TaskStatus
from app.core.subscription.store import SubscriptionStore


APP = QApplication.instance() or QApplication([])

VIDEOS = [
    {"id": "haveFile0001", "title": "Old one", "numLikes": 5, "createdAt": "2026-09-01T00:00:00Z",
     "user": {"username": "bob", "name": "Bob"}},
    {"id": "fresh0000002", "title": "Brand new", "numLikes": 50, "createdAt": "2026-09-02T00:00:00Z",
     "user": {"username": "bob", "name": "Bob"}},
    {"id": "fresh0000002", "title": "Brand new (twice)", "user": {"username": "bob"}},
]


def fake_api(name, *args, **_kwargs):
    if name == "get_user_id":
        return "uid", ""
    if name == "get_user_videos":
        return list(VIDEOS)
    if name == "get_playlist_videos":
        return list(VIDEOS[:1])
    if name == "get_video_info":
        return {"id": args[0], "title": "T", "fileUrl": "https://files/x", "file": {"id": "f"}, "numLikes": 1}, ""
    raise AssertionError(name)


class _ManagerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="iwaratool-insight-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._saved = (app_config.download_dir, app_config.skip_existing_files)
        self.addCleanup(self._restore_config)
        app_config.download_dir = os.path.join(self.tmp, "downloads")
        os.makedirs(app_config.download_dir)
        app_config.skip_existing_files = True
        self.mgr = DownloadManager()
        self.mgr.history = DownloadHistory(os.path.join(self.tmp, "history.db"))
        self.mgr.subscriptions = SubscriptionStore(os.path.join(self.tmp, "subscriptions.db"))
        self.mgr._api_call = fake_api
        self.mgr._existing_file_index_built_at = 0

    def _restore_config(self):
        app_config.download_dir, app_config.skip_existing_files = self._saved

    def _downloaded(self, video_id: str, *, with_file: bool = True) -> str:
        path = os.path.join(app_config.download_dir, f"{video_id}.mp4")
        if with_file:
            with open(path, "wb") as fh:
                fh.write(b"data")
        self.mgr.history.upsert_downloaded({"video_id": video_id, "title": video_id, "file_path": path})
        return path


class ListingPreviewTests(_ManagerCase):
    def test_only_author_and_playlist_links_are_previewed(self):
        self.assertEqual(self.mgr.listing_kind("https://www.iwara.tv/profile/bob/videos"), "user")
        self.assertEqual(self.mgr.listing_kind("https://www.iwara.tv/playlist/abc"), "playlist")
        self.assertEqual(self.mgr.listing_kind("https://www.iwara.tv/video/abc"), "")
        self.assertEqual(self.mgr.listing_kind("https://www.iwara.tv/videos?tags=x"), "")

    def test_preview_counts_what_is_here_and_dedupes(self):
        self._downloaded("haveFile0001")
        preview = self.mgr.preview_listing("https://www.iwara.tv/profile/bob")
        self.assertEqual(preview["error"], "")
        self.assertEqual(preview["kind"], "user")
        self.assertEqual([item["video_id"] for item in preview["items"]], ["haveFile0001", "fresh0000002"])
        have = {item["video_id"]: item["have"] for item in preview["items"]}
        self.assertEqual(have, {"haveFile0001": "file", "fresh0000002": ""})
        self.assertTrue(all(item["matches"] for item in preview["items"]))
        self.assertTrue(preview["items"][1]["relative_path"].endswith("fresh0000002.mp4"))
        self.assertEqual(preview["save_dir"], os.path.abspath(app_config.download_dir))

    def test_queued_and_moved_videos_are_told_apart(self):
        self._downloaded("haveFile0001", with_file=False)  # history only, the file moved away
        self.mgr._task_id_by_video_id["fresh0000002"] = "task"
        items = self.mgr.preview_listing("https://www.iwara.tv/profile/bob")["items"]
        self.assertEqual([item["have"] for item in items], ["history", "queued"])

    def test_rule_filters_are_applied_to_each_video(self):
        with patch.object(DownloadManager, "_rule_payload_for_id", return_value={
            "filter_enabled": True, "filter_min_likes_enabled": True, "filter_min_likes": 10,
        }):
            items = self.mgr.preview_listing("https://www.iwara.tv/profile/bob")["items"]
        self.assertEqual([item["matches"] for item in items], [False, True])
        self.assertIn("likes", items[0]["match_reason"])

    def test_a_listing_without_videos_reports_an_error(self):
        self.mgr._api_call = lambda name, *a, **k: ("uid", "") if name == "get_user_id" else []
        preview = self.mgr.preview_listing("https://www.iwara.tv/profile/nobody")
        self.assertTrue(preview["error"])
        self.assertEqual(preview["items"], [])


class DiagnosisTests(_ManagerCase):
    def test_a_downloaded_video_says_where_the_file_is(self):
        path = self._downloaded("haveFile0001")
        result = self.mgr.explain_not_downloaded("https://www.iwara.tv/video/haveFile0001")
        self.assertIn(path, result["verdict"])
        self.assertFalse(result["can_download"])

    def test_a_skipped_task_explains_the_rule_and_the_duplicate_trap(self):
        task = DownloadTask(task_id="t1", url="u", video_id="skipMe000001", status=TaskStatus.SKIPPED,
                            error_msg="likes 1 < 10")
        self.mgr._tasks["t1"] = task
        self.mgr._task_id_by_video_id["skipme000001"] = "t1"
        result = self.mgr.explain_not_downloaded("skipMe000001", online=False)
        self.assertIn("likes 1 < 10", result["verdict"])
        self.assertTrue(any("duplicate" in f["text"] or "重复" in f["text"] for f in result["findings"]))

    def test_mark_only_setting_is_reported(self):
        saved = app_config.download_video_file
        self.addCleanup(setattr, app_config, "download_video_file", saved)
        app_config.download_video_file = False
        result = self.mgr.explain_not_downloaded("fresh0000002", online=False)
        self.assertEqual(result["findings"][0]["level"], "block")
        self.assertFalse(result["can_download"])

    def test_nothing_in_the_way_can_be_downloaded(self):
        result = self.mgr.explain_not_downloaded("fresh0000002")
        self.assertTrue(result["can_download"])
        self.assertEqual(result["findings"][-1]["level"], "ok")

    def test_author_links_are_pointed_to_the_preview(self):
        result = self.mgr.explain_not_downloaded("https://www.iwara.tv/profile/bob")
        self.assertEqual(result["video_id"], "")
        self.assertFalse(result["can_download"])


class WatchedHistoryTests(unittest.TestCase):
    def test_watched_is_stamped_cleared_and_survives_a_re_download(self):
        tmp = tempfile.mkdtemp(prefix="iwaratool-watched-")
        self.addCleanup(shutil.rmtree, tmp, True)
        history = DownloadHistory(os.path.join(tmp, "history.db"))
        history.upsert_downloaded({"video_id": "abc", "title": "A"})
        self.assertEqual(history.get_record("abc")["watched_at"], "")
        self.assertEqual(history.set_watched(["abc", "missing"]), 1)
        self.assertTrue(history.get_record("abc")["watched_at"])
        history.upsert_downloaded({"video_id": "abc", "title": "A again"})
        self.assertTrue(history.get_record("abc")["watched_at"])
        history.set_watched(["abc"], False)
        self.assertEqual(history.list_records()[0]["watched_at"], "")


class HistoryBrowseTests(unittest.TestCase):
    def setUp(self):
        from app.ui.history import page as history_page

        self.tmp = tempfile.mkdtemp(prefix="iwaratool-browse-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        saved_dir = app_config.download_dir
        self.addCleanup(setattr, app_config, "download_dir", saved_dir)
        app_config.download_dir = self.tmp
        records = []
        for index in range(6):
            path = os.path.join(self.tmp, f"v{index}.mp4")
            if index != 5:
                with open(path, "wb") as fh:
                    fh.write(b"x")
            records.append({
                "video_id": f"vid{index}", "title": f"Video {index}", "author": "alice" if index < 4 else "bob",
                "file_path": path, "downloaded_at": "2000-01-01 00:00:00" if index == 0 else "2999-01-01 00:00:00",
                "watched_at": "2026-01-01 00:00:00" if index == 1 else "",
            })
        manager = patch.object(history_page, "download_manager")
        fake = manager.start()
        self.addCleanup(manager.stop)
        fake.get_history_records.return_value = records
        fake.subscription_cover_path.return_value = ""
        ui_values = patch.object(app_config, "set_ui_value")
        ui_values.start()
        self.addCleanup(ui_values.stop)
        self.page = history_page.HistoryInterface()
        self.page.resize(1200, 800)
        self.addCleanup(self.page.deleteLater)
        self.addCleanup(self.page.shutdown, 1000)
        self.page._set_view_mode("list")
        self.page._set_scope("all")

    def _ids(self):
        return [record["video_id"] for record in self.page._visible_records]

    def test_scopes_filter_the_records(self):
        self.assertEqual(len(self._ids()), 6)
        self.page._set_scope("unwatched")
        self.assertEqual(sorted(self._ids()), ["vid0", "vid2", "vid3", "vid4"])  # vid5's file is gone
        self.page._set_scope("recent")
        self.assertNotIn("vid0", self._ids())
        self.page._set_scope("author")
        self.assertTrue(self.page._author_list.isVisibleTo(self.page))
        bob = next(i for i in range(self.page._author_list.count())
                   if self.page._author_list.item(i).data(Qt.ItemDataRole.UserRole) == "bob")
        self.page._author_list.setCurrentRow(bob)
        self.assertEqual(sorted(self._ids()), ["vid4", "vid5"])

    def test_grid_and_cover_views_show_the_same_records_and_selection(self):
        self.page._set_view_mode("grid")
        grid = self.page._cover_grid
        self.assertIs(self.page._view_stack.currentWidget(), grid)
        self.assertEqual(grid.count(), 6)
        grid.item(2).setSelected(True)
        self.assertEqual(self.page._selected_video_ids(), [grid.item(2).data(Qt.ItemDataRole.UserRole)])
        self.page._set_view_mode("cover")
        self.assertEqual(grid.count(), 6)
        self.assertEqual(len(self.page._selected_video_ids()), 1)  # kept across the switch
        self.page._set_view_mode("list")
        self.assertEqual(self.page._table.rowCount(), 6)


class ScrollTests(unittest.TestCase):
    def test_card_grid_scrolls_under_half_a_row_per_notch(self):
        from app.ui.card_list import CardGridList

        grid = CardGridList()
        self.addCleanup(grid.deleteLater)
        grid.setViewMode(CardGridList.ViewMode.IconMode)
        grid.setGridSize(QSize(300, 246))
        for index in range(40):
            grid.addItem(QListWidgetItem(str(index)))
        grid.resize(900, 600)
        grid.show()
        APP.processEvents()
        notch = grid.verticalScrollBar().singleStep() * QApplication.wheelScrollLines() * grid.SMOOTH_STEP_RATIO
        self.assertLess(notch, 246 * 0.5)
        self.assertGreater(notch, 246 * 0.25)

    def test_user_scroll_input_is_counted(self):
        from qfluentwidgets import ScrollArea

        from app.ui.scroll_intent import UserScrollWatcher

        area = ScrollArea()
        self.addCleanup(area.deleteLater)
        watcher = UserScrollWatcher(area)
        area.verticalScrollBar().setValue(10)  # code scrolling does not count
        self.assertEqual(watcher.serial, 0)
        wheel = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPointF(0, 0).toPoint(), QPointF(0, -120).toPoint(),
                            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
        QApplication.sendEvent(area.viewport(), wheel)
        self.assertGreater(watcher.serial, 0)  # any change tells a pending restore to stand down


if __name__ == "__main__":
    unittest.main()
