"""app.bootstrap: one start/stop cycle in a temporary data folder writes the settings."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

SCRIPT = textwrap.dedent(
    """
    from app import bootstrap
    bootstrap.restore_pending_backup()
    from app.config import app_config
    bootstrap.start(frontend="test")
    app_config.ui_font_family = "Bootstrap Test Font"
    bootstrap.stop(wait=True)
    bootstrap.stop(wait=True)  # a second stop is a no-op
    print("stopped")
    """
)


class BootstrapTests(unittest.TestCase):
    def test_start_and_stop_write_the_settings_file(self):
        with tempfile.TemporaryDirectory() as data:
            env = dict(os.environ, IWARATOOL_DATA_DIR=data, IWARATOOL_DOWNLOAD_DIR=str(Path(data) / "dl"))
            env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(ROOT), env.get("PYTHONPATH", "")]))
            done = subprocess.run(
                [sys.executable, "-c", SCRIPT], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(done.returncode, 0, done.stderr[-2000:])
            self.assertIn("stopped", done.stdout)
            config = Path(data) / "config.ini"
            self.assertTrue(config.is_file())
            self.assertIn("Bootstrap Test Font", config.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
