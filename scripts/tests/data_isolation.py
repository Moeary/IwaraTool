"""Tests never read or write the real data folder.

Since v1.3.0 both interfaces share the user's data folder (macOS
``~/Library/Application Support/IwaraTool``), so a test opening the default
history.db or config.ini would migrate, fill or reconfigure the user's real
files, and the user's settings would change what the tests see.  Each test
package calls ``isolate()`` before anything imports ``app.config``; an
explicit ``IWARATOOL_DATA_DIR`` (CI, a developer's choice) is kept.
"""
from __future__ import annotations

import atexit
import os
import shutil
import tempfile


def isolate() -> None:
    if os.environ.get("IWARATOOL_DATA_DIR"):
        return
    folder = tempfile.mkdtemp(prefix="iwaratool-tests-")
    os.environ["IWARATOOL_DATA_DIR"] = os.path.join(folder, "data")
    os.environ.setdefault("IWARATOOL_DOWNLOAD_DIR", os.path.join(folder, "download"))
    atexit.register(shutil.rmtree, folder, True)
