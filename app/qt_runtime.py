"""Qt runtime tweaks that must happen before a feature first touches its plugins."""
from __future__ import annotations

import os
import sys

_done = False


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
