import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QWidget

from app.ui.window_drag import WindowsTitleBarDragFilter


class FakeTitleBar(QWidget):
    def __init__(self):
        super().__init__()
        self.handle = Mock()
        self.handle.startSystemMove.return_value = True
        self.canDrag = Mock(return_value=True)
        self.legacy_moves = 0

    def windowHandle(self):
        return self.handle

    def mouseMoveEvent(self, event):
        self.legacy_moves += 1


def mouse_event(kind, button=Qt.MouseButton.NoButton, buttons=Qt.MouseButton.NoButton):
    return QMouseEvent(kind, QPointF(80, 20), QPointF(-1200, 200), button, buttons, Qt.KeyboardModifier.NoModifier)


class WindowDragTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.bar = FakeTitleBar()
        self.filter = WindowsTitleBarDragFilter(self.bar)

    def tearDown(self):
        self.bar.close()
        self.bar.deleteLater()
        self.app.processEvents()

    def test_drag_starts_once_and_queued_moves_do_not_restart_it(self):
        QApplication.sendEvent(self.bar, mouse_event(
            QEvent.Type.MouseButtonPress, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        ))
        for buttons in (Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton):
            for _ in range(5):
                QApplication.sendEvent(self.bar, mouse_event(QEvent.Type.MouseMove, buttons=buttons))
        self.bar.handle.startSystemMove.assert_called_once_with()
        self.assertEqual(self.bar.legacy_moves, 0)

    def test_right_press_and_caption_buttons_do_not_start_drag(self):
        self.filter.eventFilter(self.bar, mouse_event(QEvent.Type.MouseButtonPress, Qt.MouseButton.RightButton))
        self.bar.canDrag.return_value = False
        self.assertFalse(self.filter.eventFilter(self.bar, mouse_event(
            QEvent.Type.MouseButtonPress, Qt.MouseButton.LeftButton,
        )))
        self.bar.handle.startSystemMove.assert_not_called()

    def test_double_click_and_release_are_left_to_title_bar(self):
        for kind in (QEvent.Type.MouseButtonDblClick, QEvent.Type.MouseButtonRelease):
            self.assertFalse(self.filter.eventFilter(self.bar, mouse_event(kind, Qt.MouseButton.LeftButton)))
        self.bar.handle.startSystemMove.assert_not_called()

    def test_unsupported_native_move_uses_one_fallback(self):
        self.bar.handle.startSystemMove.return_value = False
        with patch("qframelesswindow.utils.startSystemMove") as fallback:
            self.filter.eventFilter(self.bar, mouse_event(QEvent.Type.MouseButtonPress, Qt.MouseButton.LeftButton))
            self.filter.eventFilter(self.bar, mouse_event(QEvent.Type.MouseMove, buttons=Qt.MouseButton.LeftButton))
            fallback.assert_called_once_with(self.bar, QPointF(-1200, 200).toPoint())


if __name__ == "__main__":
    unittest.main()
