"""Loading every Flet module pulls in neither the PySide6 interface nor Qt (P3 acceptance)."""

from __future__ import annotations

import importlib
import importlib.util
import subprocess
import sys
import unittest

HAS_FLET = importlib.util.find_spec("flet") is not None

_PROBE = r"""
import importlib, pkgutil, sys
import app.ui_flet as root
for info in pkgutil.walk_packages(root.__path__, "app.ui_flet."):
    importlib.import_module(info.name)
bad = sorted(name for name in sys.modules
             if name == "app.ui" or name.startswith("app.ui.")
             or name.split(".")[0] in {"PySide6", "shiboken6", "qfluentwidgets", "qframelesswindow"})
print("\n".join(bad))
"""


@unittest.skipUnless(HAS_FLET, "Flet is not installed in this environment")
class RuntimeImportTests(unittest.TestCase):
    def test_flet_modules_do_not_load_the_qt_interface(self):
        # A fresh interpreter: this test process may already hold app.ui from other suites.
        result = subprocess.run([sys.executable, "-c", _PROBE], capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertEqual(result.stdout.strip(), "", f"loaded: {result.stdout}")


if __name__ == "__main__":
    unittest.main()
