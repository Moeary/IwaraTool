from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
import zipfile

from app.core import backup

CONFIG = (
    "[General]\n"
    "download_dir=D:/videos\n"
    "username=alice\n"
    "password=hunter2\n"
    "auth_token=tok123\n"
    "aria2_rpc_token=secret\n"
    "max_concurrent=5\n"
    "\n[ui]\n"
    "search_sort=date\n"
)


def _write(path, text):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def _make_db(path, rows):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE downloaded (video_id TEXT PRIMARY KEY)")
    conn.executemany("INSERT INTO downloaded VALUES (?)", [(r,) for r in rows])
    conn.commit()
    conn.close()


def _rows(path):
    conn = sqlite3.connect(path)
    try:
        return sorted(r[0] for r in conn.execute("SELECT video_id FROM downloaded"))
    finally:
        conn.close()


class CredentialHandlingTests(unittest.TestCase):
    def test_strip_sensitive_removes_only_credentials(self):
        stripped = backup.strip_sensitive(CONFIG)
        for secret in ("alice", "hunter2", "tok123", "secret"):
            self.assertNotIn(secret, stripped)
        self.assertIn("max_concurrent=5", stripped)
        self.assertIn("[ui]", stripped)

    def test_merge_keeps_current_login_over_restored_one(self):
        current = "[General]\nusername=bob\nauth_token=newtoken\nmax_concurrent=1\n"
        merged = backup.merge_credentials(backup.strip_sensitive(CONFIG), current)
        self.assertIn("username=bob", merged)
        self.assertIn("auth_token=newtoken", merged)
        self.assertIn("max_concurrent=5", merged)
        self.assertNotIn("max_concurrent=1", merged)
        self.assertNotIn("alice", merged)
        self.assertLess(merged.index("[General]"), merged.index("username=bob"))
        self.assertLess(merged.index("username=bob"), merged.index("[ui]"))


class RoundTripTests(unittest.TestCase):
    def test_backup_excludes_credentials_and_caches(self):
        with tempfile.TemporaryDirectory() as root:
            data = os.path.join(root, "data")
            os.makedirs(os.path.join(data, "img"))
            _write(os.path.join(data, "config.ini"), CONFIG)
            _write(os.path.join(data, "tasks.json"), "[]")
            _write(os.path.join(data, "img", "cover.jpg"), "x")
            _make_db(os.path.join(data, "history.db"), ["a1", "b2"])
            target = os.path.join(root, "out.zip")

            stored = backup.create_backup(data, target)

            self.assertEqual(sorted(stored), ["config.ini", "history.db", "tasks.json"])
            with zipfile.ZipFile(target) as archive:
                names = set(archive.namelist())
                self.assertIn(backup.MANIFEST_NAME, names)
                self.assertFalse(any(n.startswith("img") for n in names))
                self.assertNotIn("hunter2", archive.read("config.ini").decode())

    def test_stage_and_apply_restores_data_but_keeps_current_login(self):
        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, "source")
            os.makedirs(source)
            _write(os.path.join(source, "config.ini"), CONFIG)
            _make_db(os.path.join(source, "history.db"), ["old1", "old2"])
            archive_path = os.path.join(root, "b.zip")
            backup.create_backup(source, archive_path)

            data = os.path.join(root, "data")
            os.makedirs(data)
            _write(os.path.join(data, "config.ini"), "[General]\nusername=bob\nauth_token=live\nmax_concurrent=1\n")
            _make_db(os.path.join(data, "history.db"), ["new1"])
            # A stale WAL from the live database must not shadow the restored file.
            _write(os.path.join(data, "history.db-wal"), "stale")

            staged = backup.stage_restore(archive_path, data)
            self.assertCountEqual(staged, ["config.ini", "history.db"])
            self.assertTrue(backup.has_pending_restore(data))

            applied = backup.apply_pending_restore(data)

            self.assertCountEqual(applied, ["config.ini", "history.db"])
            self.assertFalse(backup.has_pending_restore(data))
            self.assertEqual(_rows(os.path.join(data, "history.db")), ["old1", "old2"])
            self.assertFalse(os.path.exists(os.path.join(data, "history.db-wal")))
            with open(os.path.join(data, "config.ini"), encoding="utf-8") as fh:
                config = fh.read()
            self.assertIn("max_concurrent=5", config)
            self.assertIn("username=bob", config)
            self.assertNotIn("alice", config)
            safety_root = os.path.join(data, backup.SAFETY_DIR_NAME)
            kept = os.path.join(safety_root, os.listdir(safety_root)[0])
            self.assertEqual(_rows(os.path.join(kept, "history.db")), ["new1"])

    def test_apply_without_pending_is_a_noop(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(backup.apply_pending_restore(root), [])


class ValidationTests(unittest.TestCase):
    def _zip(self, root, members):
        path = os.path.join(root, "x.zip")
        with zipfile.ZipFile(path, "w") as archive:
            for name, content in members.items():
                archive.writestr(name, content)
        return path

    def test_rejects_non_backups_and_path_traversal(self):
        with tempfile.TemporaryDirectory() as root:
            data = os.path.join(root, "data")
            os.makedirs(data)
            plain = self._zip(root, {"config.ini": "x"})
            with self.assertRaises(backup.BackupError):
                backup.stage_restore(plain, data)

            evil = self._zip(root, {backup.MANIFEST_NAME: "m", "../evil.txt": "x"})
            with self.assertRaises(backup.BackupError):
                backup.stage_restore(evil, data)
            self.assertFalse(os.path.exists(os.path.join(root, "evil.txt")))

            empty = self._zip(root, {backup.MANIFEST_NAME: "m"})
            with self.assertRaises(backup.BackupError):
                backup.stage_restore(empty, data)

            junk = os.path.join(root, "junk.zip")
            _write(junk, "not a zip")
            with self.assertRaises(backup.BackupError):
                backup.stage_restore(junk, data)


if __name__ == "__main__":
    unittest.main()
