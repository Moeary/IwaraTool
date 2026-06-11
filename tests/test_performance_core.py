import os
import shutil
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.config import app_config
from app.core.history import DownloadHistory
from app.core.manager import DownloadManager, download_manager
from app.core.models import DownloadTask, TaskStatus
from app.ui.download_page import DownloadInterface
from app.ui.task_page import TaskCenterInterface

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


def tearDownModule():
    for path in TEMP_DIRS:
        shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
