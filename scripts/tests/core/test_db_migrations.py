"""history.db schema steps (core/db): baseline v1.2.3, then one small step per version."""

import os
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.db import migrate as db_migrate
from app.core.db.migrate import check_steps, latest_version, migrate, schema_version
from app.core.db.migrations import STEPS, Step
from app.core.db.migrations.baseline import columns
from app.core.db.migrations.baseline import downloaded, items, sources


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="iwaratool-migrate-")
        self.path = os.path.join(self._tmp, "history.db")

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def connect(self):
        return closing(sqlite3.connect(self.path))

    def assert_current(self, conn):
        self.assertEqual(schema_version(conn), latest_version())
        self.assertTrue(set(downloaded.LATER_COLUMNS) <= columns(conn, "downloaded"))
        self.assertTrue(set(sources.LATER_COLUMNS) | {"site"} <= columns(conn, "sources"))
        self.assertTrue(set(items.LATER_COLUMNS) <= columns(conn, "items"))
        indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        self.assertIn("downloaded_by_date", indexes)

    def test_steps_are_numbered_without_gaps(self):
        check_steps(STEPS)
        self.assertEqual(STEPS[0].version, 1)
        with self.assertRaises(ValueError):
            check_steps((STEPS[0], Step(3, "skips two", lambda conn: None)))

    def test_new_file(self):
        with self.connect() as conn:
            self.assertEqual(migrate(conn), latest_version())
            self.assert_current(conn)

    def test_v0_1_file_is_caught_up_and_keeps_its_rows(self):
        with self.connect() as conn:
            conn.execute("CREATE TABLE downloaded (video_id TEXT PRIMARY KEY, downloaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
            conn.executemany("INSERT INTO downloaded(video_id) VALUES (?)", [("a",), ("b",)])
            conn.commit()
            migrate(conn)
            self.assert_current(conn)
            rows = conn.execute("SELECT video_id, title, storage FROM downloaded ORDER BY video_id").fetchall()
            self.assertEqual(rows, [("a", "", "local"), ("b", "", "local")])

    def test_sources_without_site_are_rebuilt_keeping_ids(self):
        with self.connect() as conn:
            conn.execute(
                "CREATE TABLE sources (id INTEGER PRIMARY KEY AUTOINCREMENT, source_type TEXT NOT NULL, "
                "source_key TEXT NOT NULL, title TEXT DEFAULT '', remote_id TEXT DEFAULT '', enabled INTEGER DEFAULT 1, "
                "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, last_checked_at TEXT DEFAULT '', "
                "UNIQUE(source_type, source_key))"
            )
            conn.execute("INSERT INTO sources(id, source_type, source_key, title) VALUES (7, 'user', 'alice', 'Alice')")
            conn.execute("INSERT INTO sources(id, source_type, source_key) VALUES (9, 'feed', 'me')")
            conn.execute("CREATE TABLE items (source_id INTEGER NOT NULL, video_id TEXT NOT NULL, is_new INTEGER DEFAULT 1, "
                         "PRIMARY KEY(source_id, video_id))")
            conn.execute("INSERT INTO items(source_id, video_id) VALUES (7, 'v1')")
            conn.commit()
            migrate(conn)
            self.assert_current(conn)
            rows = conn.execute("SELECT id, source_key, site, source_origin FROM sources ORDER BY id").fetchall()
            self.assertEqual(rows, [(7, "alice", "tv", "local"), (9, "me", "tv", "account")])
            self.assertEqual(conn.execute("SELECT source_id, stats_known FROM items").fetchall(), [(7, 0)])
            # The same author can now be followed on the other site.
            conn.execute("INSERT INTO sources(source_type, source_key, site) VALUES ('user', 'alice', 'ai')")

    def test_v1_2_3_file_only_gets_the_new_steps(self):
        with self.connect() as conn:
            for part in (downloaded, sources, items):
                part.create_or_catch_up(conn)  # what v1.2.3 left: the tables, no version
            conn.execute("INSERT INTO downloaded(video_id, title) VALUES ('x', 'kept')")
            conn.commit()
            migrate(conn)
            self.assert_current(conn)
            self.assertEqual(conn.execute("SELECT title FROM downloaded").fetchone(), ("kept",))

    def test_running_again_changes_nothing(self):
        with self.connect() as conn:
            migrate(conn)
            before = conn.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall()
            migrate(conn)
            self.assertEqual(conn.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall(), before)

    def test_failed_step_rolls_back_and_resumes_later(self):
        def broken(conn):
            conn.execute("CREATE TABLE half_done (x)")
            raise RuntimeError("disk full")

        steps = (STEPS[0], Step(2, "broken", broken))
        with self.connect() as conn:
            with self.assertRaises(RuntimeError):
                migrate(conn, steps)
            self.assertEqual(schema_version(conn), 1)  # the baseline stays applied
            self.assertNotIn("half_done", {r[0] for r in conn.execute("SELECT name FROM sqlite_master")})
            self.assertEqual(migrate(conn), latest_version())

    def test_file_from_a_newer_build_is_left_alone(self):
        with self.connect() as conn:
            migrate(conn)
            conn.execute(f"PRAGMA user_version={latest_version() + 5}")
            conn.commit()
            self.assertEqual(migrate(conn), latest_version() + 5)

    def test_stores_open_through_the_steps(self):
        from app.core.history import DownloadHistory
        from app.core.subscription.store import SubscriptionStore

        db_migrate._done.discard(self.path)
        DownloadHistory(self.path)
        SubscriptionStore(self.path, legacy_db_path="")
        with self.connect() as conn:
            self.assert_current(conn)
        os.remove(self.path)  # removed while running: the next use recreates it
        DownloadHistory(self.path).is_downloaded("x")
        with self.connect() as conn:
            self.assert_current(conn)


if __name__ == "__main__":
    unittest.main()
