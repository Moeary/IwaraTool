"""Wait for Qt state changes rather than assuming native events are immediate."""
import time
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


def wait_until(condition, timeout_ms=2000):
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if condition():
            return
        QTest.qWait(10)
    raise AssertionError("Qt state did not become ready before the deadline")


def activate_window(widget):
    window = widget.window()
    window.raise_()
    window.activateWindow()
    wait_until(window.isActiveWindow)
