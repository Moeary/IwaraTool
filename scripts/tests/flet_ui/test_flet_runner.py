"""The Flet launcher avoids the Nuitka build that flet_desktop would mistake for its client."""

from __future__ import annotations

import tempfile
import unittest
from unittest import mock
from pathlib import Path

from app.ui_flet import runner
from app.ui_flet.runner import absolutize_path_variables, foreign_client_folder, prepare_environment


class ForeignClientTests(unittest.TestCase):
    def test_empty_or_missing_build_folders_are_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertIsNone(foreign_client_folder(root))
            (root / "build" / "macos").mkdir(parents=True)
            self.assertIsNone(foreign_client_folder(root))

    def test_a_built_app_is_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "build" / "macos" / "IwaraTool.app").mkdir(parents=True)
            self.assertEqual(foreign_client_folder(root), root / "build" / "macos")

    def test_other_build_output_does_not_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "build" / "flet-src").mkdir(parents=True)
            self.assertIsNone(foreign_client_folder(root))


class PathVariableTests(unittest.TestCase):
    def test_relative_paths_become_absolute_and_others_stay(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp).resolve()
            env = {"IWARATOOL_DATA_DIR": "data-flet", "IWARATOOL_DOWNLOAD_DIR": str(cwd / "dl"), "OTHER": "x"}
            absolutize_path_variables(cwd, env)
            self.assertEqual(env["IWARATOOL_DATA_DIR"], str(cwd / "data-flet"))
            self.assertEqual(env["IWARATOOL_DOWNLOAD_DIR"], str(cwd / "dl"))
            self.assertEqual(env["OTHER"], "x")

    def test_unset_variables_stay_unset(self):
        env: dict[str, str] = {}
        absolutize_path_variables(Path.cwd(), env)
        self.assertEqual(env, {})


class PrepareEnvironmentTests(unittest.TestCase):
    """The data folder is Classic's (app/paths.py), except on phones."""

    def test_source_checkout_shares_the_classic_folder(self):
        env: dict[str, str] = {}
        with mock.patch.object(runner.sys, "platform", "win32"):
            prepare_environment(Path("/repo"), env)
        self.assertEqual(env["IWARATOOL_NO_QT"], "1")
        self.assertEqual(env["IWARATOOL_DATA_DIR"], str(Path("/repo") / "data"))
        self.assertEqual(env["IWARATOOL_DOWNLOAD_DIR"], str(Path("/repo") / "download"))

    def test_macos_leaves_the_folder_to_paths(self):
        env = {"FLET_PLATFORM": "macos", "FLET_APP_STORAGE_DATA": "/store"}
        with mock.patch.object(runner.sys, "platform", "darwin"):
            prepare_environment(Path("/bundle"), env)
        self.assertNotIn("IWARATOOL_DATA_DIR", env)  # ~/Library/Application Support/IwaraTool

    def test_packaged_windows_app_keeps_data_beside_the_program(self):
        env = {"FLET_PLATFORM": "windows", "FLET_APP_STORAGE_DATA": "C:/store"}
        with mock.patch.object(runner.sys, "platform", "win32"), \
                mock.patch.object(runner, "host_executable", return_value=Path("D:/Tools/IwaraTool/IwaraTool.exe")):
            prepare_environment(Path("C:/bundle/app"), env)
        self.assertEqual(env["IWARATOOL_DATA_DIR"], str(Path("D:/Tools/IwaraTool") / "data"))
        self.assertEqual(env["IWARATOOL_DOWNLOAD_DIR"], str(Path("D:/Tools/IwaraTool") / "download"))

    def test_phones_use_flet_storage(self):
        env = {"FLET_PLATFORM": "android", "FLET_APP_STORAGE_DATA": "/store"}
        prepare_environment(Path("/bundle"), env)
        self.assertEqual(env["IWARATOOL_DATA_DIR"], str(Path("/store") / "IwaraTool"))

    def test_explicit_data_dir_wins(self):
        env = {"IWARATOOL_DATA_DIR": "/mine", "FLET_PLATFORM": "macos", "FLET_APP_STORAGE_DATA": "/store"}
        prepare_environment(Path("/repo"), env)
        self.assertEqual(env["IWARATOOL_DATA_DIR"], "/mine")


class EntryOrderTests(unittest.TestCase):
    def test_staged_restore_is_applied_before_the_settings_are_read(self):
        """Importing app.config reads config.ini; a restore applied after it would be written over."""
        source = (Path(__file__).resolve().parents[3] / "main_flet.py").read_text(encoding="utf-8")
        self.assertLess(source.index("bootstrap.restore_pending_backup()"), source.index("from app.config import"))


if __name__ == "__main__":
    unittest.main()
