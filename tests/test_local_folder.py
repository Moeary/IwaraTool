"""Issue #28: use the saved path, including old author naming layouts."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget
from qfluentwidgets import RoundMenu

from app.core.download.history_actions import HistoryActionsMixin
from app.ui.media.local_folder import add_local_folder_action


class LocalFolderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "old_account" / "视频 with spaces.mp4"
        self.path.parent.mkdir()
        self.path.touch()
        self.manager = SimpleNamespace(history=Mock(), _open_system_path=Mock(return_value=(True, "")))
        self.manager.history.get_record.return_value = {"file_path": str(self.path)}
        self.manager.open_history_output = lambda *args, **kwargs: HistoryActionsMixin.open_history_output(
            self.manager, *args, **kwargs
        )
        self.parent = QWidget()
        self.addCleanup(self.parent.deleteLater)
        self.menu = RoundMenu(parent=self.parent)

    def test_action_opens_the_saved_folder_even_if_current_naming_differs(self):
        add_local_folder_action(self.menu, self.parent, self.manager, "video1")
        self.assertEqual(len(self.menu.actions()), 1)
        self.menu.actions()[0].trigger()
        self.manager._open_system_path.assert_called_once_with(str(self.path.parent))
        self.manager.history.get_record.assert_called_with("video1")

    def test_missing_and_unrecorded_files_have_no_action(self):
        self.path.unlink()
        add_local_folder_action(self.menu, self.parent, self.manager, "video1")
        self.assertEqual(self.menu.actions(), [])
        self.manager.history.get_record.return_value = None
        add_local_folder_action(self.menu, self.parent, self.manager, "video2")
        self.assertEqual(self.menu.actions(), [])

    def test_file_removed_after_menu_open_reports_failure(self):
        add_local_folder_action(self.menu, self.parent, self.manager, "video1")
        self.path.unlink()
        with patch("app.ui.media.local_folder.InfoBar.warning") as warning:
            self.menu.actions()[0].trigger()
        warning.assert_called_once()
        self.manager._open_system_path.assert_not_called()


if __name__ == "__main__":
    unittest.main()
