"""Memory use: capped decoding, lazy pages, bounded caches and trimming while idle."""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from unittest import mock
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QSettings, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication, QWidget

from app.config import app_config
from app.core import memory
from app.ui.media_card import image_size, read_pixmap


def _jpg(directory: str, name: str, width: int, height: int) -> str:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor("#336699"))
    painter = QPainter(image)
    painter.setBrush(QColor("#cc8844"))
    painter.drawEllipse(width // 4, height // 4, width // 2, height // 2)
    painter.end()
    path = os.path.join(directory, name)
    image.save(path, "JPG", 80)
    return path


class _QtCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def run(self, result=None):
        outcome = super().run(result)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        return outcome

    def pump(self, until=lambda: False, seconds: float = 3.0):
        deadline = time.time() + seconds
        while not until() and time.time() < deadline:
            self.app.processEvents()
            time.sleep(0.002)
        self.app.processEvents()


class ReadPixmapTests(_QtCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_a_large_picture_is_decoded_no_wider_than_asked(self):
        path = _jpg(self.tmp.name, "big.jpg", 3200, 2000)
        pixmap = read_pixmap(path, 800)
        self.assertEqual(pixmap.width(), 800)
        self.assertEqual(pixmap.height(), 500)  # aspect ratio kept

    def test_a_small_picture_is_left_alone(self):
        path = _jpg(self.tmp.name, "small.jpg", 300, 200)
        pixmap = read_pixmap(path, 800)
        self.assertEqual((pixmap.width(), pixmap.height()), (300, 200))

    def test_no_limit_means_full_size(self):
        path = _jpg(self.tmp.name, "full.jpg", 640, 360)
        self.assertEqual(read_pixmap(path).width(), 640)

    def test_missing_or_broken_files_give_a_null_pixmap(self):
        self.assertTrue(read_pixmap(os.path.join(self.tmp.name, "nope.jpg"), 100).isNull())
        broken = os.path.join(self.tmp.name, "broken.jpg")
        with open(broken, "wb") as handle:
            handle.write(b"not an image")
        self.assertTrue(read_pixmap(broken, 100).isNull())

    def test_size_comes_from_the_header(self):
        path = _jpg(self.tmp.name, "hdr.jpg", 1280, 720)
        self.assertEqual(image_size(path), QSize(1280, 720))
        self.assertFalse(image_size(os.path.join(self.tmp.name, "nope.jpg")).isValid())


class GalleryLazyDecodeTests(_QtCase):
    def setUp(self):
        from app.ui.media_detail import GalleryImage

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = _jpg(self.tmp.name, "pic.jpg", 3200, 2000)
        self.image = GalleryImage({"id": "f1", "name": "pic.jpg", "width": 3200, "height": 2000})
        self.addCleanup(self.image.deleteLater)
        self.image.resize(1000, 625)

    def test_pointing_at_a_file_decodes_nothing_until_it_is_painted(self):
        self.image.set_source(self.path, final=True)
        self.assertFalse(self.image.is_decoded())
        self.assertAlmostEqual(self.image._ratio, 2000 / 3200)  # the height is known from the header
        self.image.grab()
        self.assertTrue(self.image.is_decoded())

    def test_the_decoded_picture_is_capped_and_sized_for_display(self):
        from app.ui.media_detail import GALLERY_DECODE_WIDTH

        self.image.set_source(self.path, final=True)
        self.image.grab()
        self.assertLessEqual(self.image._pixmap.width(), GALLERY_DECODE_WIDTH)
        self.assertLess(self.image._pixmap.width(), 3200)

    def test_released_pictures_come_back_when_painted_again(self):
        self.image.set_source(self.path, final=True)
        self.image.grab()
        self.image.release()
        self.assertFalse(self.image.is_decoded())
        self.image.grab()
        self.assertTrue(self.image.is_decoded())

    def test_a_thumbnail_does_not_replace_the_final_picture(self):
        thumb = _jpg(self.tmp.name, "thumb.jpg", 320, 200)
        self.image.set_source(self.path, final=True)
        self.image.set_source(thumb, final=False)
        self.assertEqual(self.image._path, self.path)

    def test_an_unreadable_file_changes_nothing(self):
        self.image.set_source(os.path.join(self.tmp.name, "missing.jpg"), final=True)
        self.assertEqual(self.image._path, "")

    def test_set_pixmap_still_works_for_pictures_that_are_already_in_memory(self):
        pixmap = read_pixmap(self.path, 800)
        self.image.set_pixmap(pixmap, final=True)
        self.assertTrue(self.image.is_decoded())
        self.image.release()  # nothing on disk to come back to: it keeps what it has
        self.assertTrue(self.image.is_decoded())


class DetailReleaseTests(_QtCase):
    def setUp(self):
        from app.ui import media_detail
        from app.ui.home_workers import CoverFetcher

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        status = mock.patch("app.ui.author_status.download_manager", Mock(find_author_subscription=Mock(return_value=None)))
        status.start()
        self.addCleanup(status.stop)
        self.view = media_detail.DetailView(CoverFetcher())
        self.view.resize(1200, 700)
        self.view.show()
        self.addCleanup(self._close)
        path = _jpg(self.tmp.name, "pic.jpg", 1600, 1000)
        self.images = []
        for index in range(12):
            image = media_detail.GalleryImage({"id": f"f{index}", "name": "p.jpg", "width": 1600, "height": 1000}, self.view)
            image.set_source(path, final=True)
            self.view._gallery_box.addWidget(image)
            self.view._gallery.append(image)
            self.images.append(image)
        self.pump(seconds=0.4)
        self.view._body.activate()
        self.view._scroll.widget().layout().activate()
        self.view._scroll.widget().adjustSize()

    def _close(self):
        self.view.shutdown(500)
        self.view.deleteLater()

    def test_pictures_far_from_the_viewport_are_released(self):
        for image in self.images:
            image.grab()  # paint everything once: all decoded
        self.assertTrue(all(image.is_decoded() for image in self.images))
        self.view._scroll.verticalScrollBar().setValue(0)
        self.view._release_far_pictures()
        decoded = [image for image in self.images if image.is_decoded()]
        self.assertGreater(len(decoded), 0)  # the ones on screen stay
        self.assertLess(len(decoded), 12)  # the far ones are gone
        self.assertTrue(self.images[0].is_decoded())
        self.assertFalse(self.images[-1].is_decoded())

    def test_scrolling_schedules_the_release(self):
        self.view._scroll.verticalScrollBar().setValue(50)
        self.assertTrue(self.view._release_timer.isActive() or self.view._scroll.verticalScrollBar().value() == 0)


class SearchCoverMemoryTests(_QtCase):
    def setUp(self):
        from app.ui.search_page import SearchInterface

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        settings = QSettings(os.path.join(self.tmp.name, "config.ini"), QSettings.Format.IniFormat)
        for target, value in (
            ("app.config.app_config._qs", settings),
            ("app.ui.search_page.download_manager", Mock(history=Mock(get_records=Mock(return_value={})), get_search_tag_suggestions=Mock(return_value=[]))),
            ("app.ui.rules_page.RulePicker.refresh_rules", lambda *a, **k: None),
        ):
            patcher = mock.patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.page = SearchInterface()
        self.page.resize(1200, 800)
        self.page.show()
        self.app.processEvents()
        self.addCleanup(self._close)
        self.cover = _jpg(self.tmp.name, "cover.jpg", 1280, 720)

    def _close(self):
        self.page.shutdown(timeout_ms=1000)
        self.page.deleteLater()

    def test_covers_are_cached_at_the_card_size_not_the_file_size(self):
        self.page._image_icon(self.cover)
        cached = self.page._source_pixmaps.get(self.cover)
        self.assertIsNotNone(cached)
        self.assertLess(cached.width(), 1280)
        self.assertGreaterEqual(cached.width(), 240)

    def test_a_bigger_card_re_reads_the_file_for_more_detail(self):
        self.page._grid_icon_size = QSize(240, 135)
        self.page._image_icon(self.cover)
        small = self.page._source_pixmaps.get(self.cover).width()
        self.page._grid_icon_size = QSize(640, 360)
        icon = self.page._image_icon(self.cover)
        large = self.page._source_pixmaps.get(self.cover).width()
        self.assertGreater(large, small)
        self.assertFalse(icon.isNull())
        self.assertGreaterEqual(icon.availableSizes()[0].width(), 640)

    def test_a_file_smaller_than_asked_is_not_re_read_every_time(self):
        tiny = _jpg(self.tmp.name, "tiny.jpg", 120, 68)
        self.page._grid_icon_size = QSize(600, 340)
        with mock.patch("app.ui.search_page.read_pixmap", wraps=read_pixmap) as reader:
            for _ in range(5):
                self.page._image_icon(tiny)
        self.assertEqual(reader.call_count, 1)

    def test_a_broken_file_gives_an_empty_icon(self):
        broken = os.path.join(self.tmp.name, "broken.jpg")
        with open(broken, "wb") as handle:
            handle.write(b"junk")
        self.assertTrue(self.page._image_icon(broken).isNull())

    def test_the_cover_cache_stays_within_its_budget(self):
        from app.ui.media_card import PixmapLRU

        self.page._source_pixmaps = PixmapLRU(max_bytes=3 * 300 * 170 * 4)
        for index in range(20):
            path = _jpg(self.tmp.name, f"c{index}.jpg", 640, 360)
            self.page._image_icon(path)
        self.assertLessEqual(len(self.page._source_pixmaps), 4)


class SettingsLazyBuildTests(_QtCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        settings = QSettings(os.path.join(self.tmp.name, "config.ini"), QSettings.Format.IniFormat)
        patcher = mock.patch("app.config.app_config._qs", settings)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _page(self, *, cached_login):
        from app.ui import settings_page

        manager = Mock()
        manager.restore_cached_login.return_value = cached_login
        manager.api.token = "t"
        patcher = mock.patch.object(settings_page, "download_manager", manager)
        patcher.start()
        self.addCleanup(patcher.stop)
        page = settings_page.SettingsInterface()
        self.addCleanup(lambda: (page.shutdown(timeout_ms=500), page.deleteLater()))
        return page

    def test_the_page_is_not_built_until_it_is_shown(self):
        page = self._page(cached_login=True)
        self.assertFalse(page._built)
        self.assertFalse(hasattr(page, "_settings_board"))
        page.resize(1100, 700)
        page.show()
        self.app.processEvents()
        self.assertTrue(page._built)
        self.assertTrue(hasattr(page, "_settings_board"))

    def test_the_signed_in_state_is_remembered_and_shown_once_built(self):
        page = self._page(cached_login=True)
        page.ensure_built()
        self.assertTrue(page._login_btn.isHidden())
        self.assertFalse(page._logout_btn.isHidden())
        self.assertFalse(page._login_status_lbl.text() == "")

    def test_building_twice_does_nothing_more(self):
        page = self._page(cached_login=False)
        page.ensure_built()
        board = page._settings_board
        page.ensure_built()
        self.assertIs(page._settings_board, board)

    def test_saving_before_the_page_exists_is_a_no_op(self):
        page = self._page(cached_login=True)
        page._save_settings()  # the "settings_save" shortcut can fire before the page was ever shown
        self.assertFalse(page._built)

    def test_theme_refresh_and_shortcut_jump_work_before_and_after(self):
        page = self._page(cached_login=True)
        page.refresh_theme_styles()  # not built: must not touch missing widgets
        page.show_shortcut_settings()
        self.assertTrue(page._built)
        self.assertEqual(page._settings_board.current_category(), "shortcuts")

    def test_a_saved_password_login_builds_the_form_it_reads(self):
        from app.ui import settings_page

        app_config.auth_enabled = True
        app_config.username = "alice"
        app_config.password = "secret"
        with mock.patch.object(settings_page.SettingsInterface, "_do_login") as login:
            page = self._page(cached_login=False)
        self.assertTrue(page._built)
        login.assert_called_once_with(silent=True)
        self.assertEqual(page._user_edit.text(), "alice")


class HistoryRowIdTests(_QtCase):
    def _page(self, records):
        from app.ui.history_page import HistoryInterface

        patcher = mock.patch("app.ui.history_page.download_manager")
        manager = patcher.start()
        self.addCleanup(patcher.stop)
        manager.get_history_records.return_value = records
        page = HistoryInterface()
        self.addCleanup(lambda: (page.close(), page.deleteLater()))
        page.resize(1300, 600)
        page.show()
        self.app.processEvents()
        return page, manager

    def _records(self, count):
        return [
            {"video_id": f"h{i}", "title": f"History {i}", "author": "a", "file_path": __file__, "downloaded_at": f"2026-01-0{i + 1} 00:00:00"}
            for i in range(count)
        ]

    def test_selection_reads_the_id_from_the_record_behind_the_row(self):
        page, _manager = self._page(self._records(4))
        page._table.selectRow(1)
        self.assertEqual(page._selected_video_ids(), [page._visible_records[1]["video_id"]])

    def test_cells_no_longer_carry_a_copy_of_the_id(self):
        page, _manager = self._page(self._records(3))
        item = page._table.item(0, page._COL_QUALITY if hasattr(page, "_COL_QUALITY") else 3)
        self.assertIsNone(item.data(Qt.ItemDataRole.UserRole))
        self.assertEqual(item.toolTip(), "")  # short columns carry no tooltip
        title = page._table.item(0, page._COL_TITLE)
        self.assertNotEqual(title.toolTip(), "")  # long ones still do

    def test_clicking_an_action_cell_acts_on_the_right_record(self):
        page, _manager = self._page(self._records(3))
        with mock.patch.object(page, "_open_video_url") as open_url:
            page._on_cell_clicked(2, page._COL_OPEN_URL)
        open_url.assert_called_once()
        self.assertEqual(open_url.call_args.args[0], page._visible_records[2]["video_id"])
        with mock.patch.object(page, "_remove_record") as remove:
            page._on_cell_clicked(1, page._COL_REMOVE)
        remove.assert_called_once_with(page._visible_records[1]["video_id"])

    def test_a_disabled_action_cell_does_nothing(self):
        records = self._records(2)
        records[0]["file_path"] = ""  # nothing on disk: the file/folder actions show "-"
        page, _manager = self._page(records)
        row = next(r for r, rec in enumerate(page._visible_records) if rec["video_id"] == "h0")
        item = page._table.item(row, page._COL_OPEN_FILE)
        self.assertEqual(item.text(), "-")
        with mock.patch.object(page, "_open_record") as open_record:
            page._on_cell_clicked(row, page._COL_OPEN_FILE)
        open_record.assert_not_called()

    def test_ids_past_the_end_are_empty(self):
        page, _manager = self._page(self._records(2))
        self.assertEqual(page._video_id_at(99), "")
        self.assertEqual(page._video_id_at(-1), "")


class IdleTrimTests(_QtCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from app.core.home_cache import HomeFeedCache
        from app.ui.main_window import MainWindow

        cls.tmp = tempfile.TemporaryDirectory()
        cls.patchers = []
        for target, value in (
            ("app.config.app_config._qs", QSettings(os.path.join(cls.tmp.name, "config.ini"), QSettings.Format.IniFormat)),
            ("app.ui.home_workers.FeedWorker.run", lambda worker: None),
            ("app.ui.search_page.SearchInterface._load_initial_results", lambda page: None),
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

    def test_hiding_the_window_schedules_a_trim_and_showing_cancels_it(self):
        window = self.window
        window.show()
        self.app.processEvents()
        window.hide()
        self.assertTrue(window._trim_timer.isActive())
        window.show()
        self.app.processEvents()
        self.assertFalse(window._trim_timer.isActive())

    def test_the_trim_only_runs_while_nobody_is_looking(self):
        window = self.window
        window.show()
        self.app.processEvents()
        with mock.patch("app.ui.main_window.memory.trim_memory") as trim:
            window._trim_memory_if_idle()
            trim.assert_not_called()  # the window is on screen
            window.hide()
            window._trim_memory_if_idle()
            trim.assert_called_once()

    def test_minimizing_schedules_a_trim_too(self):
        window = self.window
        window.show()
        self.app.processEvents()
        window._trim_timer.stop()
        with mock.patch.object(window, "isMinimized", return_value=True):
            window.changeEvent(QEvent(QEvent.Type.WindowStateChange))
        self.assertTrue(window._trim_timer.isActive())

    def test_trimming_never_raises(self):
        self.assertIn(memory.trim_memory(), (True, False))
        with mock.patch("app.core.memory.sys.platform", "plan9"):
            self.assertFalse(memory.trim_memory())


if __name__ == "__main__":
    unittest.main()
