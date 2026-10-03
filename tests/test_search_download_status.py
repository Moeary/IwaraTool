"""Local search download states use batched history reads without networking."""
import json
import os
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QRect, QSettings, Qt, Signal
from PySide6.QtGui import QFont, QFontDatabase, QFontMetrics
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionViewItem

from app.config import app_config
from app.core.search import SearchAuthor, SearchVideo, normalize_oreno3d_listing
from app.ui.search_download_status import (
    SearchDownloadStatus,
    SearchDownloadStatusWorker,
    iwara_history_id,
    query_download_statuses,
)
from app.ui.search_page import SearchInterface


class _FakeHistory:
    def __init__(self, records=None):
        self.records = records or {}
        self.calls = []
        self.threads = []
        self.error = None

    def get_records(self, video_ids):
        self.calls.append(tuple(video_ids))
        self.threads.append(threading.get_ident())
        if self.error is not None:
            raise self.error
        return {video_id: dict(self.records[video_id]) for video_id in video_ids if video_id in self.records}


class _FakeSignalBus(QObject):
    history_changed = Signal()
    task_status_changed = Signal(str, str)
    task_progress_updated = Signal(str, int, int, str)
    log_message = Signal(str)


def _iwara(video_id, **kwargs):
    return SearchVideo(video_id=video_id, title=video_id, **kwargs)


def _oreno(movie_id, iwara_id=""):
    video = normalize_oreno3d_listing({"id": movie_id, "title": movie_id})
    video.download_video_id = iwara_id
    return video


class SearchDownloadStatusQueryTests(unittest.TestCase):
    def test_batch_deduplicates_ids_and_checks_only_recorded_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = os.path.join(temp_dir, "video.mp4")
            with open(file_path, "wb") as stream:
                stream.write(b"video")
            history = _FakeHistory({
                "one": {"file_path": file_path},
                "same-file": {"file_path": file_path},
                "missing": {"file_path": os.path.join(temp_dir, "missing.mp4")},
                "empty": {"file_path": ""},
            })
            with patch("app.ui.search_download_status.os.path.isfile", wraps=os.path.isfile) as isfile:
                statuses = query_download_statuses(history, ["one", "same-file", "one", "missing", "empty", "new"])
            self.assertEqual(history.calls, [("one", "same-file", "missing", "empty", "new")])
            self.assertEqual(isfile.call_count, 2)
            self.assertEqual(statuses["one"], SearchDownloadStatus.DOWNLOADED)
            self.assertEqual(statuses["same-file"], SearchDownloadStatus.DOWNLOADED)
            self.assertEqual(statuses["missing"], SearchDownloadStatus.MOVED)
            self.assertEqual(statuses["empty"], SearchDownloadStatus.MOVED)
            self.assertEqual(statuses["new"], SearchDownloadStatus.NOT_DOWNLOADED)

    def test_unknown_oreno_identity_is_never_a_history_key(self):
        bridge = _oreno("12345")
        self.assertEqual(iwara_history_id(bridge), "")
        self.assertEqual(iwara_history_id(_iwara("12345")), "12345")
        bridge.iwara_url = "https://www.iwara.tv/video/real-id/title"
        self.assertEqual(iwara_history_id(bridge), "real-id")
        bridge.iwara_url = "https://other.invalid/video/false-id"
        self.assertEqual(iwara_history_id(bridge), "")
        history = _FakeHistory()
        self.assertEqual(query_download_statuses(history, []), {})
        self.assertEqual(history.calls, [])

    def test_worker_runs_history_and_file_checks_outside_gui_thread(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = os.path.join(temp_dir, "video.mp4")
            with open(file_path, "wb") as stream:
                stream.write(b"video")
            history = _FakeHistory({"one": {"file_path": file_path}})
            checking_threads = []
            real_isfile = os.path.isfile

            def check_file(path):
                checking_threads.append(threading.get_ident())
                return real_isfile(path)

            worker = SearchDownloadStatusWorker(history, ["one"], 1, 1)
            with patch("app.ui.search_download_status.os.path.isfile", side_effect=check_file):
                worker.start()
                self.assertTrue(worker.wait(3000))
            self.assertEqual(len(history.calls), 1)
            self.assertNotEqual(history.threads[0], threading.get_ident())
            self.assertEqual(checking_threads, history.threads)


class SearchDownloadStatusPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.settings = QSettings(os.path.join(self.temp_dir.name, "config.ini"), QSettings.Format.IniFormat)
        self.settings.setValue("ui_language", "zh_CN")
        self.settings.setValue("search_auto_search_enabled", False)
        self.history = _FakeHistory()
        self.bus = _FakeSignalBus()
        self.logs = []
        self.bus.log_message.connect(self.logs.append)
        self.patches = [
            patch.object(app_config, "_qs", self.settings),
            patch("app.ui.search_page.download_manager", SimpleNamespace(history=self.history)),
            patch("app.ui.search_page.signal_bus", self.bus),
        ]
        for patcher in self.patches:
            patcher.start()
        self.page = SearchInterface()
        self.page.resize(1200, 800)

    def tearDown(self):
        self.assertTrue(self.page.shutdown(timeout_ms=3000))
        self.page.close()
        self.page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        for patcher in reversed(self.patches):
            patcher.stop()
        self.settings = None
        self.temp_dir.cleanup()

    def _file(self, name="video.mp4"):
        file_path = os.path.join(self.temp_dir.name, name)
        with open(file_path, "wb") as stream:
            stream.write(b"video")
        return file_path

    def _show_videos(self, videos):
        self.page._all_videos = videos
        self.page._render_results()
        self.page.show()
        self.app.processEvents()

    def _wait_for_idle(self, query_count):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            self.app.processEvents()
            if (
                len(self.history.calls) >= query_count
                and self.page._download_status_worker is None
                and not self.page._download_status_refresh_timer.isActive()
            ):
                return
            QTest.qWait(10)
        self.fail(f"Download-status worker did not become idle; calls={self.history.calls}")

    def _wait_until(self, condition):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            self.app.processEvents()
            if condition():
                return
            QTest.qWait(10)
        self.fail("Expected worker state did not arrive")

    def _use_real_layout_fonts(self):
        previous_font = self.app.font()
        font_ids = []
        for font_path in (
            "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/segoeui.ttf",
            "C:/Windows/Fonts/segoeuib.ttf",
        ):
            if os.path.isfile(font_path):
                font_id = QFontDatabase.addApplicationFont(font_path)
                if font_id >= 0:
                    font_ids.append(font_id)

        def restore_fonts():
            self.app.setFont(previous_font)
            for font_id in font_ids:
                QFontDatabase.removeApplicationFont(font_id)

        self.addCleanup(restore_fonts)
        families = QFontDatabase.families()
        if not families:
            self.skipTest("Real fonts are unavailable for Qt caption layout")
        if "Microsoft YaHei UI" in families:
            self.app.setFont(QFont("Microsoft YaHei UI", 9))

    def _assert_card_caption_visible(self, item):
        view = self.page._results
        for selected in (False, True):
            with self.subTest(selected=selected, caption=item.text()):
                option = QStyleOptionViewItem()
                view.initViewItemOption(option)
                option.rect = view.visualItemRect(item)
                if selected:
                    option.state |= QStyle.StateFlag.State_Selected
                view.itemDelegate().initStyleOption(option, view.indexFromItem(item))
                margin = view.itemDelegate().margin
                option.rect.adjust(0, margin, 0, -margin)
                text_rect = view.style().subElementRect(
                    QStyle.SubElement.SE_ItemViewItemText, option, view
                )
                text_margin = view.style().pixelMetric(
                    QStyle.PixelMetric.PM_FocusFrameHMargin, option, view
                ) + 1
                required_height = QFontMetrics(option.font).boundingRect(
                    QRect(0, 0, max(1, text_rect.width() - 2 * text_margin), 10000),
                    Qt.TextFlag.TextWordWrap,
                    item.text(),
                ).height()
                self.assertGreaterEqual(text_rect.height(), required_height)
                self.assertLessEqual(text_rect.bottom(), option.rect.bottom())

    def test_two_sources_share_local_state_in_grid_and_list(self):
        self.history.records["same"] = {"file_path": self._file()}
        iwara = _iwara("same", downloadable=False)
        bridge = _oreno("bridge", "same")
        self._show_videos([iwara, bridge])
        self._wait_for_idle(1)
        self.assertEqual(self.history.calls, [("same",)])
        self.assertFalse(iwara.downloadable)
        self.assertFalse(bridge.downloadable)
        for video in (iwara, bridge):
            card = self.page._item_by_key[f"video:{video.video_id}"]
            self.assertIn("本地已下载", card.text())
            self.assertIn("本地已下载", card.toolTip())
        self.page._set_combo_data(self.page._view_combo, "list")
        self._wait_for_idle(1)
        column = self.page._RESULT_COLUMN_KEYS.index("download_status")
        self.assertFalse(self.page._results_table.isColumnHidden(column))
        for row in range(2):
            self.assertEqual(self.page._results_table.item(row, column).text(), "本地已下载")
        self.assertEqual(len(self.history.calls), 1)

    def test_status_caption_fits_delegate_with_real_fonts_and_wrapped_titles(self):
        self._use_real_layout_fonts()
        # A 1200px main window leaves a 1152px search page beside navigation.
        self.page.resize(1152, 800)
        self.history.records["downloaded"] = {"file_path": self._file()}
        videos = [
            SearchVideo("downloaded", "本地文件完整", author_username="creator_a"),
            SearchVideo("moved", "历史路径已失效", author_username="creator_b"),
            SearchVideo("new", "尚未下载", author_username="creator_c"),
            _oreno("unknown"),
        ]
        self.history.records["moved"] = {"file_path": os.path.join(self.temp_dir.name, "absent.mp4")}
        self._show_videos(videos)
        self._wait_for_idle(1)

        for language, title in (
            ("zh_CN", "用于检查搜索卡片折行后的本地下载状态仍完整显示的示例视频标题"),
            ("en_US", "A long video title wrapping above download status"),
            ("ja_JP", "検索カードで長いタイトルが折り返されてもダウンロード状態を表示する動画"),
        ):
            self.settings.setValue("ui_language", language)
            for video in videos:
                video.title = title
            for columns in (4, 8):
                with self.subTest(language=language, columns=columns):
                    self.page._set_combo_data(self.page._grid_columns_combo, str(columns))
                    self.page._render_results()
                    self.app.processEvents()
                    view = self.page._results
                    for index in range(view.count()):
                        self._assert_card_caption_visible(view.item(index))

    def test_author_card_caption_still_fits_with_real_fonts(self):
        self._use_real_layout_fonts()
        self.page.resize(1152, 800)
        self.page._set_combo_data(self.page._source_combo, "iwara")
        self.page._set_combo_data(self.page._scope_combo, "authors")
        self.page._all_authors = [
            SearchAuthor(
                "author-id", "creator_with_a_long_username",
                name="作者の長い表示名 / A long author name / 很长的作者显示名称",
                bio="A longer author biography with 日本語の自己紹介与中文说明。",
                video_count=12345,
            )
        ]
        self.page.show()
        for columns in (4, 8):
            with self.subTest(columns=columns):
                self.page._set_combo_data(self.page._grid_columns_combo, str(columns))
                self.page._render_results()
                self.app.processEvents()
                self._assert_card_caption_visible(self.page._results.item(0))

    def test_unknown_oreno_does_not_claim_not_downloaded(self):
        bridge = _oreno("12345")
        self.history.records[bridge.video_id] = {"file_path": self._file()}
        self._show_videos([bridge])
        self._wait_for_idle(0)
        self.assertEqual(self.history.calls, [])
        self.assertIn("尚未识别", self.page._item_by_key[f"video:{bridge.video_id}"].text())
        self.assertEqual(self.page._result_field_value(bridge, "download_status"), "尚未识别")

    def test_missing_history_file_differs_from_no_history(self):
        self.history.records["moved"] = {"file_path": os.path.join(self.temp_dir.name, "absent.mp4")}
        moved, new = _iwara("moved"), _iwara("new")
        self._show_videos([moved, new])
        self._wait_for_idle(1)
        self.assertEqual(self.page._download_status_text(moved), "已移走")
        self.assertEqual(self.page._download_status_text(new), "未下载")
        self.assertEqual(self.history.calls, [("moved", "new")])

    def test_resolved_oreno_ids_refresh_in_one_batch(self):
        bridges = [_oreno(str(index)) for index in range(8)]
        self._show_videos(bridges)
        self._wait_for_idle(0)
        self.assertEqual(self.history.calls, [])
        for index, bridge in enumerate(bridges):
            real_id = f"iwara-{index}"
            self.history.records[real_id] = {"file_path": self._file(f"{index}.mp4")}
            self.page._on_oreno_link_item({
                "generation": self.page._generation,
                "video_id": bridge.video_id,
                "link": {"id": real_id},
            })
        self._wait_for_idle(1)
        self.assertEqual(self.history.calls, [tuple(f"iwara-{index}" for index in range(8))])
        for bridge in bridges:
            self.assertEqual(self.page._download_status_text(bridge), "本地已下载")
            self.assertIn("本地已下载", self.page._item_by_key[f"video:{bridge.video_id}"].text())

    def test_history_signals_refresh_additions_and_removals_without_losing_selection(self):
        video = _iwara("one")
        self._show_videos([video])
        self.page._set_combo_data(self.page._view_combo, "list")
        self._wait_for_idle(1)
        self.page._results_table.selectRow(0)
        self.history.records["one"] = {"file_path": self._file()}
        for _ in range(8):
            self.bus.history_changed.emit()
        self._wait_for_idle(2)
        self.assertEqual(self.page._download_status_text(video), "本地已下载")
        self.assertTrue(self.page._results_table.item(0, 0).isSelected())
        self.history.records.clear()
        self.bus.history_changed.emit()
        self._wait_for_idle(3)
        self.assertEqual(self.page._download_status_text(video), "未下载")
        self.assertEqual(len(self.history.calls), 3)

    def test_only_completed_tasks_refresh_local_state(self):
        video = _iwara("one")
        self._show_videos([video])
        self._wait_for_idle(1)
        self.history.records["one"] = {"file_path": self._file()}
        for _ in range(10):
            self.bus.task_status_changed.emit("task", "downloading")
            self.bus.task_progress_updated.emit("task", 1, 10, "1 MB/s")
        QTest.qWait(180)
        self.assertEqual(len(self.history.calls), 1)
        self.bus.task_status_changed.emit("task", "completed")
        self._wait_for_idle(2)
        self.assertEqual(self.page._download_status_text(video), "本地已下载")

    def test_hidden_page_defers_history_checks_and_rechecks_file_on_show(self):
        file_path = self._file()
        self.history.records["one"] = {"file_path": file_path}
        video = _iwara("one")
        self._show_videos([video])
        self._wait_for_idle(1)
        self.page.hide()
        os.rename(file_path, file_path + ".moved")
        self.bus.history_changed.emit()
        QTest.qWait(180)
        self.assertEqual(len(self.history.calls), 1)
        self.page.show()
        self._wait_for_idle(2)
        self.assertEqual(self.page._download_status_text(video), "已移走")
        self.page.hide()
        os.rename(file_path + ".moved", file_path)
        self.page.show()
        self._wait_for_idle(3)
        self.assertEqual(self.page._download_status_text(video), "本地已下载")

    def test_failed_lookup_is_unknown_and_retries_on_history_event(self):
        self.history.error = RuntimeError("unavailable")
        video = _iwara("one")
        self._show_videos([video])
        self._wait_for_idle(1)
        QTest.qWait(180)
        self.assertEqual(len(self.history.calls), 1)
        self.assertEqual(self.page._download_status_text(video), "尚未识别")
        self.assertIn("unavailable", self.logs[0])
        self.history.error = None
        self.history.records["one"] = {"file_path": self._file()}
        self.bus.history_changed.emit()
        self._wait_for_idle(2)
        self.assertEqual(self.page._download_status_text(video), "本地已下载")

    def test_shutdown_disconnects_history_and_task_listeners(self):
        self._show_videos([_iwara("one")])
        self._wait_for_idle(1)
        self.assertTrue(self.page.shutdown(timeout_ms=3000))
        revision = self.page._download_status_revision
        self.bus.history_changed.emit()
        self.bus.task_status_changed.emit("task", "completed")
        QTest.qWait(180)
        self.assertEqual(self.page._download_status_revision, revision)
        self.assertEqual(len(self.history.calls), 1)
        self.assertIsNone(self.page._download_status_worker)

    def test_history_change_during_query_discards_old_result_and_queries_again(self):
        entered, release = threading.Event(), threading.Event()
        original_get_records = self.history.get_records

        def delayed_snapshot(video_ids):
            snapshot = original_get_records(video_ids)
            if len(self.history.calls) == 1:
                entered.set()
                release.wait(3)
            return snapshot

        video = _iwara("one")
        with patch.object(self.history, "get_records", side_effect=delayed_snapshot):
            try:
                self._show_videos([video])
                self._wait_until(entered.is_set)
                self.history.records["one"] = {"file_path": self._file()}
                self.bus.history_changed.emit()
            finally:
                release.set()
            self._wait_for_idle(2)
        self.assertEqual(len(self.history.calls), 2)
        self.assertEqual(self.page._download_status_text(video), "本地已下载")

    def test_shutdown_waits_for_running_query_and_ignores_queued_result(self):
        entered, release = threading.Event(), threading.Event()
        original_get_records = self.history.get_records

        def delayed_snapshot(video_ids):
            snapshot = original_get_records(video_ids)
            entered.set()
            release.wait(3)
            return snapshot

        with patch.object(self.history, "get_records", side_effect=delayed_snapshot):
            try:
                self._show_videos([_iwara("one")])
                self._wait_until(entered.is_set)
                worker = self.page._download_status_worker
                timer = threading.Timer(0.05, release.set)
                timer.start()
                self.assertTrue(self.page.shutdown(timeout_ms=3000))
                self.assertFalse(worker.isRunning())
            finally:
                release.set()
            self.app.processEvents()
        self.assertEqual(self.page._download_status_by_id, {})
        self.assertFalse(self.page._download_status_refresh_timer.isActive())

    def test_old_layout_keeps_fields_and_shows_added_status_column(self):
        self.assertTrue(self.page.shutdown(timeout_ms=3000))
        self.page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        saved = {"order": list(reversed(range(12))), "visible": [1, 2, 3]}
        app_config.set_ui_value("search_result_table_v2_columns", json.dumps(saved))
        self.page = SearchInterface()
        header = self.page._results_table.horizontalHeader()
        order = [header.logicalIndex(index) for index in range(13)]
        self.assertEqual(order[:12], saved["order"])
        visible = [index for index in range(13) if not self.page._results_table.isColumnHidden(index)]
        self.assertEqual(visible, [1, 2, 3, 12])


if __name__ == "__main__":
    unittest.main()
