"""Logging, SQLite helper, config descriptors and X-Version salt overrides."""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

from app import logging_setup
from app.config import AppConfig, app_config
from app.core import api as api_module
from app.core.sqlite_utils import connect


class SqliteHelperTests(unittest.TestCase):
    def test_connections_use_wal_and_busy_timeout(self):
        with tempfile.TemporaryDirectory() as root:
            conn = connect(os.path.join(root, "t.db"))
            try:
                self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
                self.assertGreaterEqual(conn.execute("PRAGMA busy_timeout").fetchone()[0], 30000)
            finally:
                conn.close()

    def test_second_writer_waits_instead_of_failing(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "t.db")
            first = connect(path)
            first.execute("CREATE TABLE t (v INTEGER)")
            first.commit()
            first.execute("BEGIN IMMEDIATE")
            first.execute("INSERT INTO t VALUES (1)")
            second = connect(path)
            second.execute("PRAGMA busy_timeout=50")
            try:
                with self.assertRaises(sqlite3.OperationalError):
                    second.execute("BEGIN IMMEDIATE")
            finally:
                first.rollback()
                first.close()
                second.close()


class LoggingTests(unittest.TestCase):
    def test_file_handler_writes_and_child_loggers_propagate(self):
        root = logging.getLogger("iwaratool")
        saved_handlers, saved_flag = list(root.handlers), logging_setup._configured
        saved_hooks = (logging_setup.sys.excepthook, logging_setup.threading.excepthook)
        try:
            root.handlers.clear()
            logging_setup._configured = False
            with tempfile.TemporaryDirectory() as directory:
                logging_setup.setup_logging(directory=directory)
                logging_setup.get_logger("app.core.demo").warning("hello %s", "log")
                for handler in root.handlers:
                    handler.flush()
                    handler.close()
                root.handlers.clear()
                with open(os.path.join(directory, logging_setup.LOG_FILE_NAME), encoding="utf-8") as fh:
                    content = fh.read()
            self.assertIn("iwaratool.core.demo", content)
            self.assertIn("hello log", content)
        finally:
            root.handlers[:] = saved_handlers
            logging_setup._configured = saved_flag
            logging_setup.sys.excepthook, logging_setup.threading.excepthook = saved_hooks


class ConfigTests(unittest.TestCase):
    def test_plain_settings_are_descriptors_and_coerce_bools(self):
        self.assertTrue(hasattr(AppConfig, "request_min_interval_ms"))
        saved = app_config.minimize_to_tray
        try:
            app_config.minimize_to_tray = 1
            self.assertIs(app_config.minimize_to_tray, True)
            app_config.minimize_to_tray = False
            self.assertIs(app_config.minimize_to_tray, False)
        finally:
            app_config.minimize_to_tray = saved

    def test_legacy_migration_never_copies_account_fields(self):
        self.assertTrue({"username", "password", "auth_enabled"} <= AppConfig._LEGACY_UNMIGRATED_KEYS)
        self.assertNotIn("auth_token", AppConfig._LEGACY_UNMIGRATED_KEYS)

    def test_every_default_has_an_accessor(self):
        missing = [key for key in AppConfig._DEFAULTS if not hasattr(AppConfig, key)]
        self.assertEqual(missing, [])


class SaltOverrideTests(unittest.TestCase):
    def test_builtin_salts_stay_available_after_overrides(self):
        saved = app_config.x_version_salts
        try:
            app_config.x_version_salts = "custom-one, custom-two;custom-one"
            salts = api_module.x_version_salts()
            self.assertEqual(salts[:2], ("custom-one", "custom-two"))
            for builtin in api_module._X_VERSION_SALTS:
                self.assertIn(builtin, salts)
            self.assertEqual(len(salts), len(set(salts)))
        finally:
            app_config.x_version_salts = saved

    def test_json_file_override(self):
        with tempfile.TemporaryDirectory() as directory:
            with open(os.path.join(directory, api_module.SALTS_OVERRIDE_FILE), "w", encoding="utf-8") as fh:
                json.dump({"salts": ["from-file"]}, fh)
            with mock.patch.object(type(app_config), "app_data_dir", new_callable=mock.PropertyMock, return_value=directory):
                self.assertEqual(api_module.x_version_salts()[0], "from-file")

    def test_unreadable_override_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            with open(os.path.join(directory, api_module.SALTS_OVERRIDE_FILE), "w", encoding="utf-8") as fh:
                fh.write("{not json")
            with mock.patch.object(type(app_config), "app_data_dir", new_callable=mock.PropertyMock, return_value=directory):
                self.assertEqual(api_module.x_version_salts()[-len(api_module._X_VERSION_SALTS):], api_module._X_VERSION_SALTS)


if __name__ == "__main__":
    unittest.main()
