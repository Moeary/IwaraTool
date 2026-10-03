"""History notifications expose committed, readable state to UI consumers."""
from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from contextlib import closing

from PySide6.QtCore import Qt

from app.core.history import DownloadHistory
from app.signal_bus import signal_bus


class HistoryNotificationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="iwara-history-events-")
        self.addCleanup(self.directory.cleanup)
        self.history = DownloadHistory(os.path.join(self.directory.name, "history.db"))
        self.snapshots = []

        def on_changed():
            # A UI slot may immediately read the same store. Never notify
            # before commit or while its non-reentrant lock is held.
            locked = self.history._lock.locked()
            self.snapshots.append((locked, None if locked else self.history.list_records()))

        self.listener = on_changed
        signal_bus.history_changed.connect(self.listener, Qt.ConnectionType.DirectConnection)
        self.addCleanup(signal_bus.history_changed.disconnect, self.listener)

    def test_import_rename_and_removal_notify_with_committed_paths(self):
        self.history.add_downloaded("first")
        self.assertEqual(self.snapshots[-1][1][0]["video_id"], "first")

        self.history.upsert_downloaded({"video_id": "first", "file_path": "original.mp4"})
        self.assertEqual(self.snapshots[-1][1][0]["file_path"], "original.mp4")

        self.history.update_file_paths("first", file_path="renamed.mp4")
        self.assertEqual(self.snapshots[-1][1][0]["file_path"], "renamed.mp4")

        self.history.upsert_downloaded({"video_id": "second"})
        before_remove = len(self.snapshots)
        self.assertEqual(self.history.remove_many(["first", "second"]), 2)
        self.assertEqual(len(self.snapshots), before_remove + 1)
        self.assertEqual(self.snapshots[-1][1], [])
        self.assertTrue(all(not locked for locked, _records in self.snapshots))

    def test_empty_changes_and_failed_write_do_not_notify(self):
        self.history.upsert_downloaded({})
        self.assertEqual(self.history.remove_many([]), 0)
        self.assertEqual(self.history.remove_many(["missing"]), 0)
        self.assertEqual(self.snapshots, [])

        with closing(sqlite3.connect(self.history._db_path)) as connection:
            connection.execute(
                "CREATE TRIGGER reject_download BEFORE INSERT ON downloaded "
                "BEGIN SELECT RAISE(ABORT, 'test write rejected'); END"
            )
            connection.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.history.upsert_downloaded({"video_id": "rejected"})
        self.assertEqual(self.snapshots, [])
        self.assertEqual(self.history.list_records(), [])

    def test_folder_cleanup_emits_one_batch_notification(self):
        for video_id in ("first", "second"):
            self.history.upsert_downloaded({"video_id": video_id})
        self.snapshots.clear()

        result = self.history.sync_with_download_folder(self.directory.name)

        self.assertEqual(result["removed"], 2)
        self.assertEqual(self.snapshots, [(False, [])])


if __name__ == "__main__":
    unittest.main()
