"""The Flet launcher avoids the Nuitka build that flet_desktop would mistake for its client."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.ui_flet.runner import absolutize_path_variables, foreign_client_folder


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


if __name__ == "__main__":
    unittest.main()
