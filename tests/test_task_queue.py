import json
import os
import tempfile
import unittest

from app.core.manager import DownloadManager
from app.core.models import DownloadTask, TaskStatus
from app.core.task_queue import TASK_QUEUE_SCHEMA_VERSION, TaskQueueStore


class TaskQueueStoreTests(unittest.TestCase):
    def test_round_trip_keeps_recoverable_tasks_and_requeues_active_work(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "tasks.json")
            store = TaskQueueStore(path)
            store.save(
                [
                    DownloadTask(
                        "queued",
                        "https://www.iwara.tv/video/queued-video",
                        "queued-video",
                        title="排队任务",
                    ),
                    DownloadTask(
                        "active",
                        "https://www.iwara.tv/video/active-video",
                        "active-video",
                        status=TaskStatus.DOWNLOADING,
                        download_url="https://expired.example/video.mp4",
                        file_path=os.path.join(temp_dir, "active.mp4"),
                        downloaded_bytes=1024,
                        total_bytes=4096,
                        cancel_requested=True,
                        aria2_gid="old-gid",
                    ),
                    DownloadTask(
                        "failed",
                        "https://www.iwara.tv/video/failed-video",
                        "failed-video",
                        status=TaskStatus.FAILED,
                        error_msg="network error",
                    ),
                    DownloadTask(
                        "completed",
                        "https://www.iwara.tv/video/completed-video",
                        "completed-video",
                        status=TaskStatus.COMPLETED,
                    ),
                ]
            )

            with open(path, "r", encoding="utf-8") as stream:
                raw = json.load(stream)
            self.assertEqual(raw["version"], TASK_QUEUE_SCHEMA_VERSION)

            loaded = {task.task_id: task for task in store.load()}
            self.assertEqual(set(loaded), {"queued", "active", "failed"})
            self.assertEqual(loaded["queued"].title, "排队任务")
            self.assertEqual(loaded["active"].status, TaskStatus.QUEUED_META)
            self.assertEqual(loaded["active"].download_url, "")
            self.assertFalse(loaded["active"].cancel_requested)
            self.assertEqual(loaded["active"].aria2_gid, "")
            self.assertEqual(loaded["failed"].status, TaskStatus.FAILED)
            self.assertEqual(loaded["failed"].error_msg, "network error")

    def test_corrupt_snapshot_is_ignored_with_diagnostic(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "tasks.json")
            with open(path, "w", encoding="utf-8") as stream:
                stream.write("{broken")

            store = TaskQueueStore(path)
            self.assertEqual(store.load(), [])
            self.assertTrue(store.last_error)


class DownloadManagerPersistenceTests(unittest.TestCase):
    def test_manager_rebuilds_queue_and_terminal_indexes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TaskQueueStore(os.path.join(temp_dir, "tasks.json"))
            store.save(
                [
                    DownloadTask(
                        "active",
                        "https://www.iwara.tv/video/active-video",
                        "active-video",
                        status=TaskStatus.RESOLVING,
                    ),
                    DownloadTask(
                        "cancelled",
                        "https://www.iwara.tv/video/cancelled-video",
                        "cancelled-video",
                        status=TaskStatus.CANCELLED,
                    ),
                ]
            )

            manager = DownloadManager(task_store=store)
            try:
                self.assertEqual(manager.get_task("active").status, TaskStatus.QUEUED_META)
                self.assertIn("active", manager._queued_meta_ids)
                self.assertIn("cancelled", manager._terminal_task_id_set)
                self.assertEqual(manager.pending_task_count(), 1)
            finally:
                manager.shutdown(wait=False)

    def test_shutdown_preserves_temp_file_and_requeues_running_task(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TaskQueueStore(os.path.join(temp_dir, "tasks.json"))
            final_path = os.path.join(temp_dir, "video.mp4")
            temp_path = f"{final_path}_temp"
            with open(temp_path, "wb") as stream:
                stream.write(b"partial download")

            manager = DownloadManager(task_store=store)
            task = DownloadTask(
                "running",
                "https://www.iwara.tv/video/running-video",
                "running-video",
                status=TaskStatus.DOWNLOADING,
                file_path=final_path,
                download_url="https://expired.example/video.mp4",
            )
            with manager._lock:
                manager._tasks[task.task_id] = task
                manager._task_id_by_video_id[task.video_id] = task.task_id
                manager._active_task_ids.add(task.task_id)

            self.assertEqual(manager.shutdown(wait=False), 1)
            self.assertTrue(os.path.exists(temp_path))

            restored = store.load()
            self.assertEqual(len(restored), 1)
            self.assertEqual(restored[0].status, TaskStatus.QUEUED_META)
            self.assertEqual(restored[0].file_path, final_path)
            self.assertFalse(restored[0].cancel_requested)

    def test_shutdown_blocks_follow_up_queue_activation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TaskQueueStore(os.path.join(temp_dir, "tasks.json"))
            manager = DownloadManager(task_store=store)
            queued = DownloadTask(
                "queued",
                "https://www.iwara.tv/video/queued-video",
                "queued-video",
            )
            with manager._lock:
                manager._tasks[queued.task_id] = queued
                manager._task_id_by_video_id[queued.video_id] = queued.task_id
                manager._queued_meta_ids.append(queued.task_id)

            manager.shutdown(wait=False)
            manager._try_activate()

            self.assertEqual(queued.status, TaskStatus.QUEUED_META)
            self.assertNotIn(queued.task_id, manager._active_task_ids)


if __name__ == "__main__":
    unittest.main()
