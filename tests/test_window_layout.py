"""Regressions for hidden pages forcing the window beyond a scaled screen."""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from app.ui.main_window import MainWindow


class WindowLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.previous_window = MainWindow._window_ref
        self.window = MainWindow()
        self.window.stackedWidget.setAnimationEnabled(False)
        self.window.resize(1100, 800)
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        # Close the test view without shutting down the shared application
        # manager needed by other suites in the same interpreter.
        self.window._reloading_language = True
        self.window.close()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        MainWindow._window_ref = self.previous_window

    def test_hidden_pages_fit_a_1600_pixel_screen_at_175_percent(self):
        # 1600 / 1.75 gives 914 logical pixels; reserve space for the taskbar.
        # The old repair form forced 930+ even on the default download page.
        self.assertLess(self.window.minimumSizeHint().height(), 880)
        self.assertLess(self.window.minimumHeight(), 880)

    def test_repair_form_scrolls_instead_of_growing_the_main_window(self):
        repair = self.window._repair_page
        self.window.switchTo(repair)
        self.app.processEvents()
        scroll = repair._controls_scroll
        self.assertGreater(scroll.verticalScrollBar().maximum(), 0)
        self.assertLessEqual(self.window.height(), 800)
        # The last controls remain reachable, rather than being clipped.
        scroll.ensureWidgetVisible(repair._log_edit)
        self.assertGreater(scroll.verticalScrollBar().value(), 0)

    def test_moving_window_keeps_its_size_with_repair_page_hidden(self):
        initial_size = self.window.size()
        for x, y in ((80, 60), (160, 100), (100, 80)):
            self.window.move(x, y)
            self.app.processEvents()
            self.assertEqual(self.window.size(), initial_size)
            self.assertEqual(self.window.pos().toTuple(), (x, y))


if __name__ == "__main__":
    unittest.main()
