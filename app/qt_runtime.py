"""Qt runtime tweaks that must happen before a feature first touches its plugins."""
from __future__ import annotations

import os
import signal
import sys

_done = False


def install_console_interrupt(on_interrupt, parent) -> None:
    """Turn Ctrl+C in the terminal into ``on_interrupt()`` instead of a traceback.

    Qt's event loop runs in C++, so Python only sees the signal when some
    Python code runs next — often deep inside an event filter, where the
    default handler raises ``KeyboardInterrupt``.  The handler only queues the
    callback; a light timer gives the interpreter a chance to notice the
    signal while the window is idle.  A second Ctrl+C exits at once.
    """

    from PySide6.QtCore import QTimer

    def handle(_signum, _frame):
        signal.signal(signal.SIGINT, lambda *_args: os._exit(130))
        QTimer.singleShot(0, on_interrupt)

    signal.signal(signal.SIGINT, handle)
    timer = QTimer(parent)
    timer.setInterval(250)
    timer.timeout.connect(lambda: None)
    timer.start()


def ensure_multimedia_plugins() -> None:
    """Make Qt find the multimedia backend in conda/pixi layouts.

    The conda PySide6 keeps plugins under ``<prefix>/Library/lib/qt6/plugins``
    (Windows) or ``<prefix>/lib/qt6/plugins``, which Qt does not search by
    default, leaving QMediaPlayer without any decoder.
    """
    global _done
    if _done:
        return
    _done = True
    from PySide6.QtCore import QCoreApplication

    candidates = [
        os.path.join(sys.prefix, "Library", "lib", "qt6", "plugins"),
        os.path.join(sys.prefix, "lib", "qt6", "plugins"),
    ]
    try:
        import PySide6

        candidates.append(os.path.join(os.path.dirname(PySide6.__file__), "plugins"))
    except Exception:
        pass
    for candidate in candidates:
        if os.path.isdir(os.path.join(candidate, "multimedia")):
            QCoreApplication.addLibraryPath(candidate)
