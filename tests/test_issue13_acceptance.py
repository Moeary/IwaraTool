"""Cross-page acceptance for repair, search history state, and author actions."""
from __future__ import annotations

import os
import tempfile
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QSettings
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.config import app_config
from app.core.history import DownloadHistory
from app.core.manager import download_manager
from app.core.search import SearchVideo
from app.core.subscriptions import SubscriptionStore
from app.ui.main_window import MainWindow
from app.ui.search_download_status import SearchDownloadStatus, download_status_text
from app.ui.subscription_page import SubscriptionInterface


class Issue13AcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.context = ExitStack()
        self.addCleanup(self.context.close)
        self.directory = Path(self.context.enter_context(tempfile.TemporaryDirectory(prefix="iwara-issue13-")))
        settings = QSettings(str(self.directory / "config.ini"), QSettings.Format.IniFormat)
        self.context.enter_context(patch.object(app_config, "_qs", settings))
        app_config.search_auto_search_enabled = False
        app_config.desktop_notifications_enabled = False
        self.history = DownloadHistory(str(self.directory / "history.db"))
        self.subscriptions = SubscriptionStore(str(self.directory / "history.db"))
        self.context.enter_context(patch.object(download_manager, "history", self.history))
        self.context.enter_context(patch.object(download_manager, "subscriptions", self.subscriptions))
        self.context.enter_context(patch.object(download_manager, "_tasks", {}))
        self.context.enter_context(patch.object(SubscriptionInterface, "_start_avatar_worker_for_missing_sources"))
        self.refresh = self.context.enter_context(patch.object(SubscriptionInterface, "_start_refresh"))
        self.context.enter_context(patch("app.ui.search_page.InfoBar.success"))
        self.previous_window = MainWindow._window_ref
        self.window = MainWindow()
        self.window.stackedWidget.setAnimationEnabled(False)
        self.window.resize(1200, 850)
        self.window.show()
        self.page = self.window._search_page
        self.window.switchTo(self.page)
        self.app.processEvents()
        self.addCleanup(self._close_window)

    def _close_window(self):
        self.window._reloading_language = True
        self.window.close()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        MainWindow._window_ref = self.previous_window

    def wait_for(self, condition, message):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            self.app.processEvents()
            if condition():
                return
            QTest.qWait(10)
        self.fail(message)

    def show_videos(self, videos):
        self.page._all_videos = list(videos)
        self.page._render_results()
        self.app.processEvents()

    def test_repair_history_updates_both_search_sources_and_subscription(self):
        folder = self.directory / "creator"
        folder.mkdir()
        original = folder / "old.mp4"
        original.write_bytes(b"local media fixture")
        video_id = "LocalVideo123"
        source_id = self.subscriptions.add_source("author", "creator", "Creator", "user-1")
        self.subscriptions.upsert_items(source_id, [{"video_id": video_id, "title": "Demo"}])
        direct = SearchVideo(video_id, "Demo", source_kind="iwara")
        bridge = SearchVideo("oreno3d:123", "Demo", source_kind="oreno3d", download_video_id=video_id)
        self.show_videos([direct, bridge])
        not_downloaded = download_status_text(SearchDownloadStatus.NOT_DOWNLOADED)
        self.wait_for(lambda: self.page._download_status_text(direct) == not_downloaded, "initial status was not resolved")

        summary = download_manager.repair_folder_files(
            [{
                "path": str(original), "status": "ready", "video_id": video_id,
                "metadata": {},
                "cached_meta": {
                    "video_id": video_id, "title": "Demo", "author": "creator",
                    "published_at": "2026-01-02T00:00:00Z",
                },
            }],
            options={"filename_template": "{author}/{YYYY-MM-DD}_{title}.mp4", "add_to_history": True},
        )
        self.assertEqual(summary["failed"], 0)
        renamed = folder / "2026-01-02_Demo.mp4"
        self.assertEqual(renamed.read_bytes(), b"local media fixture")
        self.assertEqual(self.history.get_record(video_id)["file_path"], str(renamed))
        self.assertTrue(download_manager.get_subscription_items(source_id)[0]["downloaded"])
        downloaded = download_status_text(SearchDownloadStatus.DOWNLOADED)
        self.wait_for(lambda: all(self.page._download_status_text(v) == downloaded for v in (direct, bridge)), "repair history was not reflected in search")
        self.assertIn(downloaded, self.page._results.item(0).text())
        self.assertIn(downloaded, self.page._results.item(1).text())

        self.page._set_combo_data(self.page._view_combo, "list")
        column = self.page._RESULT_COLUMN_KEYS.index("download_status")
        self.assertEqual(self.page._results_table.item(0, column).text(), downloaded)
        self.assertEqual(self.page._results_table.item(1, column).text(), downloaded)

        self.history.remove(video_id)
        self.wait_for(lambda: self.page._results_table.item(0, column).text() == not_downloaded, "history removal was not reflected in the list")

    def test_add_subscription_and_go_selects_one_source_without_duplicate_refresh(self):
        video = SearchVideo("Video123", "Demo", raw={"user": {"username": "creator", "name": "Creator", "id": "user-1"}})
        self.show_videos([video])
        self.page._results.item(0).setSelected(True)
        self.page._subscribe_selected_author_and_go()
        self.app.processEvents()

        sources = self.subscriptions.list_sources()
        self.assertEqual(len(sources), 1)
        source_id = sources[0]["id"]
        self.assertIs(self.window.stackedWidget.currentWidget(), self.window._subscription_page)
        self.assertEqual(self.window._subscription_page._current_source_id, source_id)
        self.wait_for(lambda: self.window._subscription_page._selected_source_id() == source_id, "subscription source was not selected after rendering")
        self.refresh.assert_called_once_with(source_id)

        self.window.switchTo(self.page)
        self.page._results.item(0).setSelected(True)
        self.page._subscribe_selected_author_and_go()
        self.app.processEvents()
        self.assertEqual(len(self.subscriptions.list_sources()), 1)
        self.assertEqual(self.refresh.call_count, 2)
        self.assertIs(self.window.stackedWidget.currentWidget(), self.window._subscription_page)

    def test_view_author_works_uses_user_id_without_adding_subscription(self):
        video = SearchVideo("Video123", "Demo", raw={"user": {"username": "creator", "name": "Creator", "id": "user-1"}})
        self.show_videos([video])
        self.page._results.item(0).setSelected(True)
        with patch.object(download_manager, "get_search_video_page", return_value=(
            [{"id": "Work456", "title": "Another work", "user": {"username": "creator", "id": "user-1"}}],
            1, False, "",
        )) as request:
            self.page._view_selected_author_works()
            self.wait_for(lambda: bool(self.page._all_videos) and self.page._all_videos[0].video_id == "Work456", "author-scoped result did not arrive")
            self.assertEqual(request.call_args.args[0]["user"], "user-1")
            self.assertNotIn("q", request.call_args.args[0])
        self.assertEqual(self.subscriptions.list_sources(), [])
        self.assertIn("creator", self.page._scope_hint.text())
        self.assertEqual(self.page._build_filters().author_id, "user-1")
        self.page._keyword_edit.setFocus()
        QTest.keyClicks(self.page._keyword_edit, "next")
        self.assertEqual(self.page._build_filters().author_id, "")
        self.assertEqual(self.page._build_filters().keyword, "next")


if __name__ == "__main__":
    unittest.main()
