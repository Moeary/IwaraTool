from __future__ import annotations

import tempfile
import unittest
from collections import deque
from pathlib import Path

from app.core.manager import DownloadManager
from app.core.models import DownloadTask
from app.core.task_queue import TaskQueueStore


class TaskPriorityTests(unittest.TestCase):
    def test_priority_round_trips_through_queue_snapshot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TaskQueueStore(str(Path(temp_dir) / "tasks.json"))
            store.save(
                [
                    DownloadTask(
                        task_id="task-low",
                        url="https://www.iwara.tv/video/low",
                        video_id="low",
                        priority=-10,
                        rule_id="rule-1",
                    )
                ]
            )
            restored = store.load()
        self.assertEqual(len(restored), 1)
        self.assertEqual(restored[0].priority, -10)
        self.assertEqual(restored[0].rule_id, "rule-1")

    def test_scheduler_pops_highest_priority_then_fifo(self):
        manager = DownloadManager.__new__(DownloadManager)
        manager._queued_meta_ids = deque(["normal-1", "high", "normal-2"])
        manager._tasks = {
            "normal-1": DownloadTask("normal-1", "u1", "v1", priority=0),
            "high": DownloadTask("high", "u2", "v2", priority=10),
            "normal-2": DownloadTask("normal-2", "u3", "v3", priority=0),
        }
        self.assertEqual(manager._pop_next_queued_task_locked(), "high")
        self.assertEqual(manager._pop_next_queued_task_locked(), "normal-1")
        self.assertEqual(manager._pop_next_queued_task_locked(), "normal-2")


if __name__ == "__main__":
    unittest.main()
