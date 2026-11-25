"""The core must import and work without PySide6 (Android, other front ends).

Each check runs in a fresh interpreter whose import system refuses PySide6,
shiboken6 and the qfluentwidgets packages, with data and download folders in
a temporary directory.
"""

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]

BLOCK_QT = textwrap.dedent(
    """
    import sys

    class _BlockQt:
        def find_spec(self, name, path=None, target=None):
            if name.split(".")[0] in ("PySide6", "shiboken6", "qfluentwidgets", "qframelesswindow"):
                raise ImportError(f"{name} is blocked for this test")

    sys.meta_path.insert(0, _BlockQt())
    """
)


class CoreWithoutQtTests(unittest.TestCase):
    def _run(self, body: str) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ)
            env.pop("IWARATOOL_NO_QT", None)
            env.update(
                IWARATOOL_DATA_DIR=os.path.join(tmp, "data"),
                IWARATOOL_DOWNLOAD_DIR=os.path.join(tmp, "download"),
                PYTHONPATH=str(PROJECT_ROOT),
            )
            script = BLOCK_QT + textwrap.dedent(body)
            done = subprocess.run(
                [sys.executable, "-c", script],
                cwd=tmp,
                env=env,
                capture_output=True,
                text=True,
                timeout=120,
            )
        self.assertEqual(done.returncode, 0, done.stderr[-4000:])
        return json.loads(done.stdout.strip().splitlines()[-1])

    def test_every_core_module_imports_without_qt(self):
        result = self._run(
            """
            import importlib, json, pkgutil
            import app.core

            failed = {}
            for module in pkgutil.walk_packages(app.core.__path__, "app.core."):
                try:
                    importlib.import_module(module.name)
                except Exception as error:
                    failed[module.name] = repr(error)
            loaded_qt = sorted(name for name in sys.modules if name.split(".")[0] == "PySide6")
            print(json.dumps({"failed": failed, "qt": loaded_qt}))
            """
        )
        self.assertEqual(result["failed"], {})
        self.assertEqual(result["qt"], [])

    def test_presentation_and_flet_modules_import_without_qt(self):
        """app.presentation always; app.ui_flet and app.bootstrap where Flet is installed."""

        result = self._run(
            """
            import importlib, importlib.util, json, pkgutil
            import app.presentation

            packages = [app.presentation]
            names = ["app.bootstrap"]
            if importlib.util.find_spec("flet") is not None:
                import app.ui_flet
                packages.append(app.ui_flet)
            failed = {}
            for package in packages:
                names += [m.name for m in pkgutil.walk_packages(package.__path__, package.__name__ + ".")]
            for name in names:
                try:
                    importlib.import_module(name)
                except Exception as error:
                    failed[name] = repr(error)
            loaded = sorted(
                name for name in sys.modules
                if name.split(".")[0] in ("PySide6", "shiboken6", "qfluentwidgets") or name.startswith("app.ui.")
            )
            print(json.dumps({"failed": failed, "loaded": loaded, "count": len(names)}))
            """
        )
        self.assertEqual(result["failed"], {})
        self.assertEqual(result["loaded"], [])
        self.assertGreater(result["count"], 20)

    def test_settings_events_and_translations_work_without_qt(self):
        result = self._run(
            """
            import json, os
            from app.config import app_config
            from app.i18n import tr
            from app.qt_support import qt_available
            from app.settings_store import IniSettingsStore
            from app.signal_bus import signal_bus

            seen = []
            signal_bus.task_status_changed.connect(lambda task, status: seen.append([task, status]))
            signal_bus.task_status_changed.emit("t1", "done")

            app_config.max_concurrent = 5
            app_config.ui_language = "ja_JP"
            app_config.set_ui_value("sizes", "120,340")
            reread = IniSettingsStore(app_config.config_path)
            print(json.dumps({
                "qt": qt_available(),
                "store": type(app_config._qs).__name__,
                "bus": [cls.__name__ for cls in type(signal_bus).__mro__],
                "seen": seen,
                "max_concurrent": app_config.max_concurrent,
                "on_disk": [reread.value("max_concurrent"), reread.value("ui/sizes")],
                "in_data_dir": os.path.dirname(app_config.config_path) == os.environ["IWARATOOL_DATA_DIR"],
                "ja": tr("Cancel", "取消", "キャンセル"),
            }))
            """
        )
        self.assertFalse(result["qt"])
        self.assertEqual(result["store"], "IniSettingsStore")
        self.assertNotIn("QObject", result["bus"])
        self.assertEqual(result["seen"], [["t1", "done"]])
        self.assertEqual(result["max_concurrent"], 5)
        self.assertEqual(result["on_disk"], ["5", "120,340"])
        self.assertTrue(result["in_data_dir"])
        self.assertEqual(result["ja"], "キャンセル")


if __name__ == "__main__":
    unittest.main()
