"""Every module of the app imports (a fresh interpreter, so nothing is already loaded)."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import unittest

HAS_FLET = importlib.util.find_spec("flet") is not None

_PROBE = r"""
import importlib, pkgutil
import app.core, app.presentation, app.ui
failed = []
names = ["app.bootstrap"]
for package in (app.core, app.presentation, app.ui):
    names += [info.name for info in pkgutil.walk_packages(package.__path__, package.__name__ + ".")]
for name in names:
    try:
        importlib.import_module(name)
    except Exception as error:
        failed.append(f"{name}: {error!r}")
print(len(names))
print("\n".join(failed))
"""


@unittest.skipUnless(HAS_FLET, "Flet is not installed in this environment")
class RuntimeImportTests(unittest.TestCase):
    def test_every_module_imports(self):
        result = subprocess.run([sys.executable, "-c", _PROBE], capture_output=True, text=True, timeout=180)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        count, *failed = result.stdout.strip().splitlines()
        self.assertEqual(failed, [])
        self.assertGreater(int(count), 100)


if __name__ == "__main__":
    unittest.main()
