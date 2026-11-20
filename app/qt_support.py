"""Whether the Qt bindings are there, so the core can run without them.

The desktop app always has PySide6. The core (API, downloads, subscriptions)
must also import where it does not, such as an Android build that embeds
Python behind a native UI, so ``app.config`` and ``app.signal_bus`` pick a
pure-Python backend when this says no. ``IWARATOOL_NO_QT=1`` forces that
backend even with PySide6 installed (tests, tools).
"""

from __future__ import annotations

import functools
import os


@functools.cache
def qt_available() -> bool:
    if os.environ.get("IWARATOOL_NO_QT", "").strip() not in ("", "0"):
        return False
    # A real import, not importlib.util.find_spec: frozen builds (Nuitka) may
    # not answer find_spec for compiled packages, and the desktop app imports
    # QtCore right away anyway.
    try:
        import PySide6.QtCore  # noqa: F401
    except ImportError:
        return False
    return True
