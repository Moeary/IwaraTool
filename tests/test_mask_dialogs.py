from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QDialog, QWidget

from qfluentwidgets import MessageBoxBase

from app.ui import mask_dialogs


class MaskDialogExecTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)
        mask_dialogs.install()

    def _window(self, *, native_page: bool) -> QWidget:
        window = QWidget()
        window.resize(480, 320)
        page = QWidget(window)
        page.resize(480, 320)
        if native_page:
            page.winId()  # what a QVideoWidget in a scroll area does to the window
        window.show()
        self.addCleanup(window.deleteLater)
        return window

    def _exec_and_watch(self, dialog: QDialog, finish) -> tuple[int, dict]:
        seen = {}

        def look():
            seen["modal"] = dialog.testAttribute(Qt.WidgetAttribute.WA_ShowModal)
            seen["visible"] = dialog.isVisible()
            finish()

        QTimer.singleShot(50, look)
        return dialog.exec(), seen

    def test_beside_native_views_the_dialog_is_shown_without_qt_modality(self):
        window = self._window(native_page=True)
        dialog = MessageBoxBase(window)

        result, seen = self._exec_and_watch(dialog, dialog.accept)

        self.assertEqual(result, QDialog.DialogCode.Accepted)
        self.assertTrue(seen["visible"])
        self.assertFalse(seen["modal"])  # a modal native child would block its own clicks
        self.assertFalse(dialog.isVisible())

    def test_without_native_views_exec_is_unchanged(self):
        window = self._window(native_page=False)
        dialog = MessageBoxBase(window)

        result, seen = self._exec_and_watch(dialog, dialog.reject)

        self.assertEqual(result, QDialog.DialogCode.Rejected)
        self.assertTrue(seen["modal"])

    def test_deleting_the_open_dialog_ends_the_wait(self):
        window = self._window(native_page=True)
        dialog = MessageBoxBase(window)

        result, _seen = self._exec_and_watch(dialog, dialog.deleteLater)

        self.assertEqual(result, 0)

    def test_open_mask_dialog_finds_only_a_shown_dialog(self):
        window = self._window(native_page=False)
        dialog = MessageBoxBase(window)
        self.assertIsNone(mask_dialogs.open_mask_dialog(window))
        dialog.show()
        self.assertIs(mask_dialogs.open_mask_dialog(window), dialog)
        dialog.hide()
        self.assertIsNone(mask_dialogs.open_mask_dialog(window))


if __name__ == "__main__":
    unittest.main()
