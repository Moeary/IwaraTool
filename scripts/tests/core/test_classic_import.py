"""Importing the data of v1.2 or earlier (the Classic build; core/classic_import.py)."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from app.core import backup, classic_import


def _classic(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "config.ini").write_text("[General]\ndownload_dir = /x\nauth_token = secret\n", encoding="utf-8")
    (folder / "rules.json").write_text("[]", encoding="utf-8")
    return folder


class ClassicImportTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_found_automatically_only_on_macos(self):
        _classic(self.root / "Library" / "Application Support" / "IwaraTool")
        self.assertTrue(classic_import.classic_data_dir(platform="darwin", home=self.root))
        self.assertEqual(classic_import.classic_data_dir(platform="win32", home=self.root), "")

    def test_not_found_without_settings(self):
        (self.root / "Library" / "Application Support" / "IwaraTool").mkdir(parents=True)
        self.assertEqual(classic_import.classic_data_dir(platform="darwin", home=self.root), "")

    def test_picked_program_folder_or_data_folder(self):
        data = _classic(self.root / "IwaraTool" / "data")
        self.assertEqual(classic_import.find_data_dir(str(self.root / "IwaraTool")), str(data))
        self.assertEqual(classic_import.find_data_dir(str(data)), str(data))
        self.assertEqual(classic_import.find_data_dir(str(self.root)), "")
        self.assertEqual(classic_import.find_data_dir(""), "")

    def test_stages_a_restore_without_credentials(self):
        source = _classic(self.root / "classic")
        target = self.root / "flet"
        target.mkdir()
        staged = classic_import.stage_import(str(source), str(target))
        self.assertIn("config.ini", staged)
        self.assertTrue(backup.has_pending_restore(str(target)))
        applied = backup.apply_pending_restore(str(target))
        self.assertIn("rules.json", applied)
        config = (target / "config.ini").read_text(encoding="utf-8")
        self.assertIn("download_dir", config)
        self.assertNotIn("secret", config)

    def test_refuses_its_own_folder_and_non_data_folders(self):
        source = _classic(self.root / "same")
        with self.assertRaises(ValueError):
            classic_import.stage_import(str(source), os.path.join(str(source), "."))
        with self.assertRaises(ValueError):
            classic_import.stage_import(str(self.root), str(self.root / "flet"))


if __name__ == "__main__":
    unittest.main()
