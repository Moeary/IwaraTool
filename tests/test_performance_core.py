import os
import gc
import json
import shutil
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QTableWidget

from app.config import app_config
from app.core.history import DownloadHistory
from app.core.manager import DownloadManager, _compact_video_raw_json, download_manager
from app.core.models import DownloadTask, TaskStatus
from app.core.subscriptions import SubscriptionStore
from app.ui.download_page import DownloadInterface
from app.ui.task_page import TaskCenterInterface
from app.ui.ui_state import (
    apply_table_column_layout,
    connect_table_width_saver,
    restore_table_columns,
    restore_table_widths,
)

TEMP_DIRS: list[str] = []


class FakeExecutor:
    def __init__(self):
        self.submitted = []

    def submit(self, fn, *args, **kwargs):
        self.submitted.append((fn, args, kwargs))
        return None


class ConfigGuard:
    def __enter__(self):
        self.max_concurrent = app_config.max_concurrent
        self.skip_existing_files = app_config.skip_existing_files
        app_config.max_concurrent = 3
        app_config.skip_existing_files = False
        return self

    def __exit__(self, *_exc):
        app_config.max_concurrent = self.max_concurrent
        app_config.skip_existing_files = self.skip_existing_files


def make_manager() -> DownloadManager:
    mgr = DownloadManager()
    tmp_dir = tempfile.mkdtemp(prefix="iwaratool-test-")
    TEMP_DIRS.append(tmp_dir)
    mgr.history = DownloadHistory(os.path.join(tmp_dir, "history.db"))
    mgr.subscriptions = SubscriptionStore(os.path.join(tmp_dir, "subscriptions.db"))
    mgr._resolve_executor = FakeExecutor()
    return mgr


class ManagerPerformanceTests(unittest.TestCase):
    def test_bulk_enqueue_5000_dedupes_and_activates_limit(self):
        with ConfigGuard():
            mgr = make_manager()
            ids = [(f"video{i:05d}", f"https://www.iwara.tv/video/video{i:05d}") for i in range(5000)]
            summary = mgr._enqueue_video_ids_bulk(ids)

            self.assertEqual(summary["queued"], 5000)
            self.assertEqual(len(mgr.get_tasks()), 5000)
            self.assertEqual(len(mgr._task_id_by_video_id), 5000)
            self.assertEqual(len(mgr._active_task_ids), 3)
            self.assertEqual(len(mgr._queued_meta_ids), 4997)
            self.assertEqual(len(mgr._resolve_executor.submitted), 3)

    def test_duplicate_enqueue_keeps_indexes_consistent(self):
        with ConfigGuard():
            mgr = make_manager()
            summary = mgr._enqueue_video_ids_bulk(
                [
                    ("sameVideo01", "https://www.iwara.tv/video/sameVideo01"),
                    ("sameVideo01", "https://www.iwara.tv/video/sameVideo01"),
                ]
            )

            self.assertEqual(summary["queued"], 1)
            self.assertEqual(summary["duplicates"], 1)
            self.assertEqual(len(mgr.get_tasks()), 1)
            self.assertEqual(list(mgr._task_id_by_video_id.keys()), ["samevideo01"])

    def test_terminal_prune_keeps_active_and_queued(self):
        mgr = make_manager()
        mgr._terminal_keep_limit = 5
        with mgr._lock:
            queued = DownloadTask("queued", "", "queued", status=TaskStatus.QUEUED_META)
            active = DownloadTask("active", "", "active", status=TaskStatus.RESOLVING)
            mgr._tasks[queued.task_id] = queued
            mgr._tasks[active.task_id] = active
            mgr._task_id_by_video_id[queued.video_id] = queued.task_id
            mgr._task_id_by_video_id[active.video_id] = active.task_id
            mgr._queued_meta_ids.append(queued.task_id)
            mgr._active_task_ids.add(active.task_id)
            for i in range(10):
                task = DownloadTask(f"done{i}", "", f"done{i}", status=TaskStatus.COMPLETED)
                task.raw_json = "x" * 10000
                mgr._tasks[task.task_id] = task
                mgr._task_id_by_video_id[task.video_id] = task.task_id
                mgr._mark_terminal_locked(task)

        removed = mgr._prune_terminal_tasks()
        statuses = {task.task_id: task.status for task in mgr.get_tasks()}

        self.assertEqual(len(removed), 5)
        self.assertIn("queued", statuses)
        self.assertIn("active", statuses)
        self.assertEqual(sum(1 for s in statuses.values() if s == TaskStatus.COMPLETED), 5)
        self.assertEqual(len(mgr._task_id_by_video_id), 7)
        self.assertEqual(len(mgr._terminal_task_id_set), 5)
        self.assertTrue(
            all(task.raw_json == "" and task.tags_json == "" for task in mgr.get_tasks() if task.status == TaskStatus.COMPLETED)
        )

    def test_history_list_and_batch_queries_omit_heavy_fields_by_default(self):
        tmp_dir = tempfile.mkdtemp(prefix="iwaratool-history-")
        TEMP_DIRS.append(tmp_dir)
        history = DownloadHistory(os.path.join(tmp_dir, "history.db"))
        history.upsert_downloaded(
            {
                "video_id": "heavy01",
                "title": "Heavy",
                "tags_json": json.dumps([{"name": "tag"}]),
                "raw_json": json.dumps({"body": "x" * 10000}),
                "file_path": os.path.join(tmp_dir, "heavy01.mp4"),
            }
        )

        listed = history.list_records()[0]
        batched = history.get_records(["heavy01"])["heavy01"]
        full = history.get_record("heavy01", include_raw=True)

        self.assertNotIn("raw_json", listed)
        self.assertNotIn("tags_json", listed)
        self.assertNotIn("raw_json", batched)
        self.assertNotIn("tags_json", batched)
        self.assertIn("raw_json", full)
        self.assertIn("tags_json", full)

    def test_compact_raw_json_keeps_nfo_fields_without_full_payload(self):
        raw = _compact_video_raw_json(
            {
                "id": "video01",
                "title": "Title",
                "body": "video body",
                "download": {"urls": ["x" * 10000]},
                "file": {"huge": "y" * 10000},
                "user": {
                    "username": "author",
                    "profile": {"description": "author profile"},
                },
            }
        )
        data = json.loads(raw)

        self.assertEqual(data["body"], "video body")
        self.assertEqual(data["user"]["profile"]["description"], "author profile")
        self.assertNotIn("download", data)
        self.assertNotIn("file", data)
        self.assertLess(len(raw), 1000)

    def test_mark_subscription_items_downloaded_creates_moved_history(self):
        mgr = make_manager()
        source_id = mgr.subscriptions.add_source("author", "author01", "Author 01")
        mgr.subscriptions.upsert_items(
            source_id,
            [
                {
                    "video_id": "videoMoved01",
                    "title": "Moved Video",
                    "author": "author01",
                    "published_at": "2026-06-12T00:00:00Z",
                    "source_url": "https://www.iwara.tv/video/videoMoved01",
                }
            ],
        )

        marked = mgr.mark_subscription_items_downloaded(["videoMoved01"])
        record = mgr.history.get_record("videoMoved01")
        item = mgr.get_subscription_items(source_id)[0]

        self.assertEqual(marked, 1)
        self.assertEqual(record["title"], "Moved Video")
        self.assertEqual(record["file_path"], "")
        self.assertTrue(item["downloaded"])
        self.assertFalse(item["download_file_exists"])

        restored = mgr.restore_subscription_items_downloaded(["videoMoved01"])
        restored_item = mgr.get_subscription_items(source_id)[0]

        self.assertEqual(restored, 1)
        self.assertIsNone(mgr.history.get_record("videoMoved01"))
        self.assertFalse(restored_item["downloaded"])

    def test_subscription_store_migrates_legacy_db_into_history_db(self):
        tmp_dir = tempfile.mkdtemp(prefix="iwaratool-subscription-migrate-")
        TEMP_DIRS.append(tmp_dir)
        legacy_path = os.path.join(tmp_dir, "subscriptions.db")
        history_path = os.path.join(tmp_dir, "history.db")

        legacy_store = SubscriptionStore(legacy_path, legacy_db_path="")
        legacy_source_id = legacy_store.add_source("author", "author01", "Author 01")
        legacy_store.upsert_items(
            legacy_source_id,
            [
                {
                    "video_id": "legacyVideo01",
                    "title": "Legacy Video",
                    "author": "author01",
                    "published_at": "2026-06-12T00:00:00Z",
                    "source_url": "https://www.iwara.tv/video/legacyVideo01",
                }
            ],
        )
        del legacy_store
        gc.collect()

        merged_store = SubscriptionStore(history_path, legacy_db_path=legacy_path)
        sources = merged_store.list_sources()
        items = merged_store.list_items(int(sources[0]["id"]))

        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["source_key"], "author01")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["video_id"], "legacyVideo01")
        self.assertFalse(os.path.exists(legacy_path))
        self.assertTrue(any(name.startswith("subscriptions.db.migrated") for name in os.listdir(tmp_dir)))

    def test_subscription_submit_honors_metadata_only_options(self):
        mgr = make_manager()
        tmp_dir = tempfile.mkdtemp(prefix="iwaratool-subscription-metadata-")
        TEMP_DIRS.append(tmp_dir)
        source_id = mgr.subscriptions.add_source("author", "author01", "Author 01")
        mgr.subscriptions.upsert_items(
            source_id,
            [
                {
                    "video_id": "subMeta01",
                    "title": "Cached Title",
                    "author": "author01",
                    "published_at": "2026-06-12T00:00:00Z",
                    "source_url": "https://www.iwara.tv/video/subMeta01",
                }
            ],
        )

        old_download_dir = app_config.download_dir
        old_download_video = app_config.download_video_file
        old_download_thumbnail = app_config.download_thumbnail
        old_collect_nfo = app_config.collect_nfo_info
        old_mark_submitted = app_config.mark_submitted_as_downloaded

        def fake_api_call(method_name, *args, **_kwargs):
            if method_name == "get_video_info":
                return (
                    {
                        "id": args[0],
                        "title": "Metadata Only",
                        "createdAt": "2026-06-12T00:00:00Z",
                        "numLikes": 1,
                        "numViews": 2,
                        "numComments": 3,
                        "slug": "metadata-only",
                        "rating": "general",
                        "body": "description",
                        "user": {"username": "author01"},
                        "file": {"id": "file01", "duration": 120},
                        "fileUrl": "https://files.example.test/video.mp4",
                        "thumbnail": 0,
                        "tags": [{"name": "tag01"}],
                    },
                    "",
                )
            raise AssertionError(f"unexpected api call: {method_name}")

        try:
            app_config.download_dir = tmp_dir
            app_config.download_video_file = False
            app_config.download_thumbnail = False
            app_config.collect_nfo_info = True
            app_config.mark_submitted_as_downloaded = True
            mgr._api_call = fake_api_call

            result = mgr.submit_subscription_items(["subMeta01"])
            record = mgr.history.get_record("subMeta01")
            nfo_files = [
                os.path.join(dirpath, name)
                for dirpath, _, filenames in os.walk(tmp_dir)
                for name in filenames
                if name.endswith(".nfo")
            ]

            self.assertEqual(result["mode"], "metadata")
            self.assertEqual(result["queued"], 0)
            self.assertEqual(result["marked"], 1)
            self.assertEqual(result["nfo"], 1)
            self.assertEqual(len(mgr.get_tasks()), 0)
            self.assertIsNotNone(record)
            self.assertEqual(record["file_path"], "")
            self.assertEqual(record["title"], "Metadata Only")
            self.assertEqual(len(nfo_files), 1)
        finally:
            app_config.download_dir = old_download_dir
            app_config.download_video_file = old_download_video
            app_config.download_thumbnail = old_download_thumbnail
            app_config.collect_nfo_info = old_collect_nfo
            app_config.mark_submitted_as_downloaded = old_mark_submitted


class UiPerformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_log_widget_keeps_max_blocks_and_flushes_in_batches(self):
        page = DownloadInterface()
        for i in range(page._MAX_LOG_BLOCKS + 250):
            page._append_log(f"log {i}")
        while page._pending_logs:
            page._flush_logs()

        self.assertLessEqual(page._log_edit.blockCount(), page._MAX_LOG_BLOCKS)

    def test_task_progress_updates_existing_row_only(self):
        with download_manager._lock:
            download_manager._tasks.clear()
            download_manager._task_id_by_video_id.clear()
            download_manager._queued_meta_ids.clear()
            download_manager._active_task_ids.clear()
            download_manager._terminal_task_ids.clear()
            download_manager._terminal_task_id_set.clear()
            task = DownloadTask(
                task_id="active",
                url="",
                video_id="active",
                title="Active",
                status=TaskStatus.DOWNLOADING,
                total_bytes=100,
                downloaded_bytes=10,
            )
            download_manager._tasks[task.task_id] = task
            download_manager._task_id_by_video_id[task.video_id] = task.task_id

        page = TaskCenterInterface()
        self.assertEqual(page._table.rowCount(), 1)
        page._on_task_progress("active", 50, 100, "1 MB/s")
        page._flush_progress_updates()

        self.assertEqual(page._table.rowCount(), 1)
        self.assertEqual(page._table.item(0, page._COL_PROGRESS).text(), "50.0%")

    def test_task_batch_add_inserts_rows_without_full_refresh(self):
        with download_manager._lock:
            download_manager._tasks.clear()
            download_manager._task_id_by_video_id.clear()
            download_manager._queued_meta_ids.clear()
            download_manager._active_task_ids.clear()
            download_manager._terminal_task_ids.clear()
            download_manager._terminal_task_id_set.clear()

        page = TaskCenterInterface()
        infos = []
        with download_manager._lock:
            for i in range(50):
                task_id = f"task{i}"
                video_id = f"video{i}"
                task = DownloadTask(task_id=task_id, url="", video_id=video_id, title=f"Video {i}")
                download_manager._tasks[task_id] = task
                download_manager._task_id_by_video_id[video_id] = task_id
                infos.append(
                    {
                        "task_id": task_id,
                        "video_id": video_id,
                        "title": task.title,
                        "author": "",
                        "status": TaskStatus.QUEUED_META.value,
                    }
                )

        page._on_tasks_added(infos)

        self.assertEqual(page._table.rowCount(), 50)
        self.assertFalse(page._refresh_pending)
        self.assertEqual(len(page._row_by_task_id), 50)

    def test_table_width_saver_records_resize_immediately(self):
        key = f"test_table_widths_{id(self)}"
        table = QTableWidget()
        table.setColumnCount(3)
        restore_table_widths(table, key, {0: 50, 1: 60, 2: 70})
        connect_table_width_saver(table, key)

        table.setColumnWidth(1, 234)
        self.app.processEvents()
        raw = str(app_config.get_ui_value(key, "") or "")

        self.assertEqual(raw.split(","), [str(table.columnWidth(i)) for i in range(3)])

    def test_table_column_layout_persists_order_and_visibility(self):
        key = f"test_table_columns_{id(self)}"
        table = QTableWidget()
        table.setColumnCount(4)
        table.setHorizontalHeaderLabels(["A", "B", "C", "D"])

        restore_table_columns(table, key, default_visible=[0, 1, 2, 3])
        apply_table_column_layout(table, key, order=[2, 0, 3, 1], visible=[2, 3], sync=True)

        header = table.horizontalHeader()
        self.assertEqual([header.logicalIndex(i) for i in range(4)], [2, 0, 3, 1])
        self.assertFalse(table.isColumnHidden(2))
        self.assertFalse(table.isColumnHidden(3))
        self.assertTrue(table.isColumnHidden(0))
        self.assertTrue(table.isColumnHidden(1))

        restored = QTableWidget()
        restored.setColumnCount(4)
        restored.setHorizontalHeaderLabels(["A", "B", "C", "D"])
        restore_table_columns(restored, key, default_visible=[0, 1, 2, 3])
        restored_header = restored.horizontalHeader()

        self.assertEqual([restored_header.logicalIndex(i) for i in range(4)], [2, 0, 3, 1])
        self.assertFalse(restored.isColumnHidden(2))
        self.assertFalse(restored.isColumnHidden(3))
        self.assertTrue(restored.isColumnHidden(0))
        self.assertTrue(restored.isColumnHidden(1))


def tearDownModule():
    for path in TEMP_DIRS:
        shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
