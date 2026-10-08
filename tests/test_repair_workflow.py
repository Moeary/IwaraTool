"""Offline regressions for in-place/organize repair and batch file safety."""
from __future__ import annotations

import errno
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QSettings
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

from app.config import app_config
from app.core.models import DownloadTask
from app.core.repair_manager import RepairManagerMixin
from app.ui.repair_page import RepairInterface


TEMPLATE = "{author}/{YYYY-MM-DD}_{title}_{id}"


class FakeHistory:
    def __init__(self):
        self.records: dict[str, dict] = {}

    def list_records(self, **_kwargs):
        return list(self.records.values())

    def get_record(self, video_id, **_kwargs):
        return self.records.get(video_id)

    def upsert_downloaded(self, metadata):
        self.records[metadata["video_id"]] = dict(metadata)

    def update_file_paths(self, video_id, **paths):
        self.records[video_id].update(paths)


class FakeAPI:
    def __init__(self, videos):
        self.videos = videos
        self.token = None
        self.scraper = SimpleNamespace(proxies={}, close=lambda: None)

    def get_video_info(self, video_id):
        return self.videos.get(video_id), ""

    def get_videos_by_query(self, *_args, **_kwargs):
        return [], ""


class FakeRepairManager(RepairManagerMixin):
    def __init__(self):
        self.history = FakeHistory()
        self.seen_ids = []
        self.subscriptions = SimpleNamespace(
            list_items=lambda: [],
            mark_items_seen=self.seen_ids.extend,
        )
        self.videos = {}
        self.api = FakeAPI(self.videos)
        self.sidecar_calls = []

    def _api_call(self, method_name, *args, **kwargs):
        return getattr(self.api, method_name)(*args, **kwargs)

    def _history_meta_from_item(self, item, existing=None):
        result = dict(existing or {})
        result.update(item)
        result["video_id"] = str(item.get("video_id") or item.get("id") or "")
        result["published_at"] = str(item.get("published_at") or item.get("createdAt") or "")
        return result

    def _repair_task(self, video_id, metadata, cached_meta, source_url, path):
        values = cached_meta or metadata
        return DownloadTask(
            video_id, source_url, video_id,
            title=values.get("title", ""), author=values.get("author", ""),
            published_at=values.get("published_at", ""), file_path=path,
        )

    def _download_thumbnail(self, task, **_kwargs):
        self.sidecar_calls.append(("thumbnail", task.file_path))
        task.thumbnail_path = f"{os.path.splitext(task.file_path)[0]}.jpg"
        Path(task.thumbnail_path).write_bytes(b"generated-cover")
        return True

    def _write_nfo(self, task, **_kwargs):
        self.sidecar_calls.append(("nfo", task.file_path))
        Path(f"{os.path.splitext(task.file_path)[0]}.nfo").write_bytes(b"generated-nfo")
        return True


class RepairTestFixture(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="iwara-repair-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.output = self.root / "output"
        self.manager = FakeRepairManager()
        self.settings_patch = patch.object(
            app_config, "_qs",
            QSettings(str(self.root / "config.ini"), QSettings.Format.IniFormat),
        )
        self.settings_patch.start()
        self.addCleanup(self.settings_patch.stop)
        self.api_patch = patch(
            "app.core.repair_manager.IwaraAPI",
            side_effect=lambda: FakeAPI(self.manager.videos),
        )
        self.api_patch.start()
        self.addCleanup(self.api_patch.stop)

    def make_video(self, author, video_id, *, title="New title", extension=".mp4", folder=None):
        directory = folder or self.source / author / "2023" / "2023-09-10"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"iwara-id={video_id}{extension}"
        path.write_bytes(f"media:{video_id}".encode())
        self.manager.videos[video_id] = {
            "id": video_id, "title": title, "author": author,
            "createdAt": "2024-05-06T12:00:00Z",
        }
        return path

    def scan(self, **options):
        options.setdefault("filename_template", TEMPLATE)
        return self.manager.scan_repair_folder(str(self.source), **options)


class RepairWorkflowTests(RepairTestFixture):
    def test_default_keeps_three_author_and_date_directories_even_with_output(self):
        originals = [self.make_video(author, f"VideoID{index:02}") for index, author in enumerate(("Alice", "Bob", "Carol"))]
        scanned = self.scan(output_root=str(self.output))
        preview = self.manager.preview_repair_files(scanned, options={
            "filename_template": TEMPLATE, "output_root": str(self.output),
        })
        self.assertEqual([item["path"] for item in preview], [str(path) for path in originals])
        self.assertEqual([item["target_path"] for item in preview], [item["target_path"] for item in scanned])
        self.assertTrue(all(path.exists() for path in originals), "Scanning must be read-only")
        result = self.manager.repair_folder_files(preview, options={
            "filename_template": TEMPLATE, "output_root": str(self.output),
        })
        self.assertEqual(result["renamed"], 3)
        self.assertEqual(result["failed"], 0)
        for source, item in zip(originals, result["items"]):
            target = Path(item["target_path"])
            self.assertEqual(target.parent, source.parent)
            self.assertTrue(target.name.startswith("2024-05-06_"))
            self.assertEqual(target.read_bytes(), f"media:{item['video_id']}".encode())
            self.assertFalse(source.exists())
            self.assertEqual(self.manager.history.get_record(item["video_id"])["file_path"], str(target))
        self.assertFalse(self.output.exists())

    def test_explicit_organize_moves_into_template_directories(self):
        original = self.make_video("Alice", "VideoID01", extension=".webm")
        options = {"filename_template": TEMPLATE, "output_root": str(self.output), "move_to_output": True}
        scanned = self.scan(output_root=str(self.output), move_to_output=True)
        preview = self.manager.preview_repair_files(scanned, options=options)
        target = self.output / "Alice" / "2024-05-06_New title_VideoID01.webm"
        self.assertEqual(scanned[0]["target_path"], str(target))
        self.assertEqual(preview[0]["target_path"], str(target))
        result = self.manager.repair_folder_files(preview, options=options)
        self.assertEqual(result["items"][0]["target_path"], str(target))
        self.assertTrue(target.is_file())
        self.assertFalse(original.exists())

    def test_blank_organize_output_uses_scanned_source_root_in_every_phase(self):
        original = self.make_video("Alice", "VideoID01")
        options = {"filename_template": TEMPLATE, "move_to_output": True}
        scanned = self.scan(move_to_output=True)
        target = self.source / "Alice" / "2024-05-06_New title_VideoID01.mp4"
        self.assertEqual(scanned[0]["target_path"], str(target))
        preview = self.manager.preview_repair_files(scanned, options=options)
        self.assertEqual(preview[0]["target_path"], str(target))
        result = self.manager.repair_folder_files(preview, options=options)
        self.assertEqual(result["items"][0]["target_path"], str(target))
        self.assertTrue(target.exists())
        self.assertFalse(original.exists())

    def test_batch_duplicate_video_targets_block_all_owners_before_moving(self):
        originals = [
            self.make_video("Alice", "VideoID01", title="Same"),
            self.make_video("Bob", "VideoID02", title="Same"),
            self.make_video("Carol", "VideoID03", title="Unique"),
        ]
        items = self.scan(filename_template="{title}", output_root=str(self.output), move_to_output=True)
        self.assertTrue(items[0]["target_conflict"])
        self.assertTrue(items[1]["target_conflict"])
        self.assertFalse(items[2]["target_conflict"])
        observed = []

        def progress(item, index, _total):
            if index == 1:
                observed.append(all(path.exists() for path in originals))

        result = self.manager.repair_folder_files(items, options={
            "filename_template": "{title}", "output_root": str(self.output), "move_to_output": True,
        }, progress_callback=progress)
        self.assertEqual(observed, [True])
        self.assertEqual(result["failed"], 2)
        self.assertEqual(result["renamed"], 1)
        self.assertTrue(all(path.exists() for path in originals[:2]))
        self.assertFalse((self.output / "Same.mp4").exists())
        self.assertTrue((self.output / "Unique.mp4").exists())

    def test_existing_video_or_sidecar_target_is_never_overwritten(self):
        for extension in (".mp4", ".jpg", ".nfo"):
            with self.subTest(extension=extension):
                isolated = self.source / extension[1:]
                source = self.make_video("Alice", "VideoID01", folder=isolated)
                output = self.output / extension[1:]
                output.mkdir(parents=True)
                existing = output / f"New title{extension}"
                existing.write_bytes(b"keep-existing")
                options = {
                    "filename_template": "{title}", "output_root": str(output), "move_to_output": True,
                    "download_thumbnail": extension == ".jpg", "collect_nfo": extension == ".nfo",
                }
                items = self.manager.scan_repair_folder(str(isolated), **{
                    key: options[key] for key in ("filename_template", "output_root", "move_to_output")
                })
                result = self.manager.repair_folder_files(items, options=options)
                self.assertEqual(result["failed"], 1)
                self.assertTrue(source.exists())
                self.assertEqual(existing.read_bytes(), b"keep-existing")
                self.assertFalse(self.manager.sidecar_calls)

    def test_batch_sidecar_targets_collide_even_when_video_extensions_differ(self):
        for existing_sidecars in (False, True):
            with self.subTest(existing_sidecars=existing_sidecars):
                folder = self.source / str(existing_sidecars)
                first = self.make_video("Alice", "VideoID01", title="Same", folder=folder / "Alice")
                second = self.make_video("Bob", "VideoID02", title="Same", extension=".mkv", folder=folder / "Bob")
                if existing_sidecars:
                    first.with_suffix(".nfo").write_bytes(b"first-nfo")
                    second.with_suffix(".nfo").write_bytes(b"second-nfo")
                options = {
                    "filename_template": "{title}", "output_root": str(self.output), "move_to_output": True,
                    "collect_nfo": not existing_sidecars,
                }
                items = self.manager.scan_repair_folder(str(folder), filename_template="{title}", output_root=str(self.output), move_to_output=True)
                result = self.manager.repair_folder_files(items, options=options)
                self.assertEqual(result["failed"], 2)
                self.assertTrue(first.exists())
                self.assertTrue(second.exists())
                self.assertFalse(self.output.exists())

    def test_two_covers_of_one_video_cannot_overwrite_each_other(self):
        source = self.make_video("Alice", "VideoID01")
        local_cover = source.with_suffix(".jpg")
        local_cover.write_bytes(b"local")
        cached_cover = self.root / "cached.jpg"
        cached_cover.write_bytes(b"cached")
        self.manager.history.records["VideoID01"] = {
            "video_id": "VideoID01", "file_path": str(source), "thumbnail_path": str(cached_cover),
        }
        items = self.scan()
        self.assertTrue(items[0]["target_conflict"])
        result = self.manager.repair_folder_files(items, options={"filename_template": TEMPLATE})
        self.assertEqual(result["failed"], 1)
        self.assertTrue(source.exists())
        self.assertEqual(local_cover.read_bytes(), b"local")
        self.assertEqual(cached_cover.read_bytes(), b"cached")

    def test_cover_and_nfo_follow_and_existing_content_is_retained(self):
        source = self.make_video("Alice", "VideoID01")
        source.with_suffix(".jpg").write_bytes(b"cover")
        source.with_suffix(".nfo").write_bytes(b"original-nfo")
        self.manager.history.records["VideoID01"] = {
            "video_id": "VideoID01", "file_path": str(source), "thumbnail_path": str(source.with_suffix(".jpg")),
        }
        result = self.manager.repair_folder_files(self.scan(), options={
            "filename_template": TEMPLATE, "download_thumbnail": True, "collect_nfo": True,
        })
        target = Path(result["items"][0]["target_path"])
        self.assertEqual(target.with_suffix(".jpg").read_bytes(), b"cover")
        self.assertEqual(target.with_suffix(".nfo").read_bytes(), b"original-nfo")
        self.assertFalse(source.with_suffix(".jpg").exists())
        self.assertFalse(source.with_suffix(".nfo").exists())
        self.assertFalse(self.manager.sidecar_calls)
        self.assertEqual(self.manager.history.get_record("VideoID01")["thumbnail_path"], str(target.with_suffix(".jpg")))

    def test_failed_sidecar_move_rolls_back_and_later_item_completes(self):
        first = self.make_video("Alice", "VideoID01")
        second = self.make_video("Bob", "VideoID02")
        cover = first.with_suffix(".jpg")
        cover.write_bytes(b"cover")
        items = self.scan()
        original_move = self.manager._move_repair_file

        def fail_cover(source, target):
            if str(source) == str(cover):
                raise OSError("injected sidecar failure")
            return original_move(source, target)

        with patch.object(self.manager, "_move_repair_file", side_effect=fail_cover):
            result = self.manager.repair_folder_files(items, options={"filename_template": TEMPLATE})
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["renamed"], 1)
        self.assertTrue(first.exists())
        self.assertEqual(cover.read_bytes(), b"cover")
        self.assertFalse(Path(items[0]["target_path"]).exists())
        self.assertFalse(second.exists())
        self.assertTrue(Path(items[1]["target_path"]).exists())

    def test_cross_drive_fallback_exclusively_creates_its_destination(self):
        source = self.make_video("Alice", "VideoID01")
        target = source.with_name("cross-drive.mp4")
        native_method = "os.rename" if os.name == "nt" else "os.link"
        with patch(f"app.core.repair_manager.{native_method}", side_effect=OSError(errno.EXDEV, "cross-device")):
            self.manager._move_repair_file(str(source), str(target))
            self.assertFalse(source.exists())
            self.assertEqual(target.read_bytes(), b"media:VideoID01")
            source.write_bytes(b"second-source")
            with self.assertRaises(FileExistsError):
                self.manager._move_repair_file(str(source), str(target))
        self.assertEqual(source.read_bytes(), b"second-source")
        self.assertEqual(target.read_bytes(), b"media:VideoID01")

    def test_shared_cover_cannot_be_moved_by_two_items_to_the_same_target(self):
        first = self.make_video("Alice", "VideoID01", title="Same")
        second = self.make_video("Bob", "VideoID02", title="Same", extension=".mkv")
        cover = self.root / "shared.jpg"
        cover.write_bytes(b"shared-cover")
        for video_id, source in (("VideoID01", first), ("VideoID02", second)):
            self.manager.history.records[video_id] = {
                "video_id": video_id, "file_path": str(source), "thumbnail_path": str(cover),
            }
        options = {"filename_template": "{title}", "output_root": str(self.output), "move_to_output": True}
        items = self.scan(**options)
        result = self.manager.repair_folder_files(items, options=options)
        self.assertEqual(result["failed"], 2)
        self.assertTrue(first.exists())
        self.assertTrue(second.exists())
        self.assertEqual(cover.read_bytes(), b"shared-cover")
        self.assertFalse(self.output.exists())

    def test_metadata_error_does_not_stop_the_remaining_batch(self):
        self.make_video("Alice", "VideoID01")
        self.make_video("Bob", "VideoID02")
        save = self.manager.history.upsert_downloaded

        def fail_first(metadata):
            if metadata["video_id"] == "VideoID01":
                raise OSError("injected history error")
            save(metadata)

        with patch.object(self.manager.history, "upsert_downloaded", side_effect=fail_first):
            result = self.manager.repair_folder_files(self.scan(), options={"filename_template": TEMPLATE})
        self.assertEqual(result["processed"], 2)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["renamed"], 2)
        self.assertEqual(result["items"][0]["file_path"], result["items"][0]["target_path"])
        self.assertIsNotNone(self.manager.history.get_record("VideoID02"))

    def test_history_only_ignores_naming_conflicts_and_keeps_source_paths(self):
        originals = [self.make_video(author, f"VideoID{index:02}", title="Same") for index, author in enumerate(("Alice", "Bob"))]
        options = {
            "filename_template": "{title}", "output_root": str(self.output), "move_to_output": True,
            "rename": False, "add_to_history": True,
        }
        items = self.scan(filename_template="{title}", output_root=str(self.output), move_to_output=True)
        self.assertTrue(all(item["target_conflict"] for item in items))
        preview = self.manager.preview_repair_files(items, options=options)
        self.assertFalse(any(item["target_conflict"] for item in preview))
        self.assertEqual([item["target_path"] for item in preview], [str(path) for path in originals])
        result = self.manager.repair_folder_files(items, options=options)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(result["history"], 2)
        self.assertTrue(all(path.exists() for path in originals))
        self.assertFalse(self.output.exists())


class RepairInterfaceModeTests(RepairTestFixture):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        super().setUp()
        self.ui_manager_patch = patch("app.ui.repair_page.download_manager", self.manager)
        self.ui_manager_patch.start()
        self.addCleanup(self.ui_manager_patch.stop)
        self.rules_patch = patch.object(RepairInterface, "_load_rules")
        self.rules_patch.start()
        self.addCleanup(self.rules_patch.stop)
        self.pages = []

    def tearDown(self):
        for page in self.pages:
            page.shutdown()
            page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def make_page(self):
        page = RepairInterface()
        self.pages.append(page)
        page._thumbnail_check.setChecked(False)
        page._nfo_check.setChecked(False)
        return page

    def use_real_layout_font(self):
        # Some Windows offscreen runs start without any font metrics; loading
        # system fonts makes clipping regressions observable in that backend.
        previous_font = QFont(self.app.font())
        added_fonts = []

        def restore_fonts():
            self.app.setFont(previous_font)
            for font_id in added_fonts:
                QFontDatabase.removeApplicationFont(font_id)

        self.addCleanup(restore_fonts)
        if not QFontDatabase.families():
            font_dir = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
            for filename in ("msyh.ttc", "segoeui.ttf", "segoeuib.ttf"):
                font_path = font_dir / filename
                if font_path.is_file():
                    font_id = QFontDatabase.addApplicationFont(str(font_path))
                    if font_id >= 0:
                        added_fonts.append(font_id)
        families = QFontDatabase.families()
        if not families:
            self.skipTest("Real fonts are unavailable for the layout regression")
        if "Microsoft YaHei UI" not in families:
            # The pixel thresholds in this regression are calibrated against
            # Microsoft YaHei UI; other fonts (e.g. CI's Linux fallbacks) wrap
            # the hints differently without indicating a layout bug.
            self.skipTest("Microsoft YaHei UI is required for the layout regression")
        self.app.setFont(QFont("Microsoft YaHei UI", 9))

    def test_control_hints_wrap_and_all_controls_fit_in_narrow_panes(self):
        self.use_real_layout_font()
        for language in ("zh_CN", "en_US", "ja_JP"):
            app_config.ui_language = language
            page = self.make_page()
            page._folder_edit.setText(str(self.source))
            page._template_edit.setText(TEMPLATE)
            page.show()
            for width, height in ((1152, 802), (852, 592)):
                page.resize(width, height)
                for mode in (0, 1):
                    with self.subTest(language=language, size=(width, height), mode=mode):
                        page._mode_combo.setCurrentIndex(mode)
                        self.app.processEvents()
                        self.assertEqual(page.size().toTuple(), (width, height))
                        scroll = page._controls_scroll
                        self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
                        self.assertEqual(scroll.widget().width(), scroll.viewport().width())
                        for control in (
                            page._folder_edit, page._source_browse_btn, page._mode_combo,
                            page._output_edit, page._output_browse_btn, page._rule_combo,
                            page._template_edit, page._scan_btn,
                        ):
                            left = control.mapTo(scroll.widget(), control.rect().topLeft()).x()
                            self.assertGreaterEqual(left, 0)
                            self.assertLessEqual(left + control.width(), scroll.viewport().width())
                        for hint in (
                            page._folder_hint, page._mode_hint, page._rule_hint,
                            page._local_video_hint, page._summary_label,
                        ):
                            self.assertTrue(hint.wordWrap())
                            self.assertGreater(hint.fontMetrics().horizontalAdvance(hint.text()), 0)
                            self.assertGreaterEqual(hint.height(), hint.heightForWidth(hint.width()))
                        self.assertGreater(scroll.verticalScrollBar().maximum(), 0)
                        scroll.ensureWidgetVisible(page._start_btn)
                        self.assertTrue(scroll.viewport().rect().contains(
                            page._start_btn.mapTo(scroll.viewport(), page._start_btn.rect().center())
                        ))
                        scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
                        self.assertTrue(scroll.viewport().rect().contains(
                            page._log_edit.mapTo(scroll.viewport(), page._log_edit.rect().center())
                        ))
            page.hide()

    def test_legacy_output_setting_does_not_enable_moving_and_mode_is_saved(self):
        app_config.set_ui_value("repair_output_folder", str(self.output))
        first = self.make_page()
        self.assertFalse(first._options()["move_to_output"])
        self.assertEqual(first._options()["output_root"], "")
        self.assertFalse(first._output_edit.isEnabled())
        self.assertFalse(first._output_browse_btn.isEnabled())
        first._mode_combo.setCurrentIndex(1)
        self.assertEqual(app_config.get_ui_value("repair_mode"), "organize")
        self.assertTrue(first._output_edit.isEnabled())
        restored = self.make_page()
        self.assertTrue(restored._options()["move_to_output"])
        restored._mode_combo.setCurrentIndex(0)
        self.assertEqual(app_config.get_ui_value("repair_mode"), "in_place")
        self.assertFalse(restored._output_edit.isEnabled())

    def test_scan_preview_mode_switch_and_execution_share_complete_paths(self):
        source = self.make_video("Alice", "VideoID01")
        page = self.make_page()
        page._folder_edit.setText(str(self.source))
        page._output_edit.setText(str(self.output))
        page._template_edit.setText(TEMPLATE)
        with patch.object(page, "_start_worker") as start_worker:
            page._scan_folder()
        scan_worker = start_worker.call_args.args[0]
        self.assertFalse(scan_worker.move_to_output)
        self.assertEqual(scan_worker.output_root, "")
        scan_results = []
        scan_worker.result_ready.connect(scan_results.append)
        scan_worker.run()
        page._worker_mode = "scan"
        page._on_result(scan_results[0])
        scanned_target = page._items[0]["target_path"]
        self.assertEqual(Path(scanned_target).parent, source.parent)
        self.assertEqual(page._table.item(0, page._COL_FILE).text(), str(source))
        self.assertEqual(page._table.item(0, page._COL_TARGET).text(), scanned_target)
        page._mode_combo.setCurrentIndex(1)
        organized_target = page._items[0]["target_path"]
        self.assertEqual(Path(organized_target).parent, self.output / "Alice")
        self.assertEqual(page._items[0]["path"], str(source))
        page._mode_combo.setCurrentIndex(0)
        self.assertEqual(page._items[0]["target_path"], scanned_target)
        page._mode_combo.setCurrentIndex(1)
        with patch.object(page, "_start_worker") as start_worker:
            page._start_repair()
        repair_worker = start_worker.call_args.args[0]
        self.assertTrue(repair_worker.options["move_to_output"])
        completed = []
        repair_worker.result_ready.connect(completed.append)
        repair_worker.run()
        self.assertEqual(completed[0]["failed"], 0)
        self.assertEqual(completed[0]["items"][0]["target_path"], organized_target)
        self.assertTrue(Path(organized_target).is_file())
        self.assertFalse(source.exists())

