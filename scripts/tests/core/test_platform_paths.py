"""macOS stores mutable data outside signed/read-only application bundles."""
from pathlib import Path
import unittest
from unittest import mock

from app import paths


class PlatformPathTests(unittest.TestCase):
    def setUp(self):
        # The test packages point IWARATOOL_DATA_DIR at a temporary folder (data_isolation.py); these
        # tests are about the locations without an override.
        environ = mock.patch.dict(paths.os.environ)
        environ.start()
        self.addCleanup(environ.stop)
        for name in ("IWARATOOL_DATA_DIR", "IWARATOOL_DOWNLOAD_DIR"):
            paths.os.environ.pop(name, None)

    def test_macos_bundle_and_source_share_user_data_location(self):
        home = Path("test-home").resolve()
        with mock.patch.object(paths.sys, "platform", "darwin"), \
                mock.patch.object(paths.Path, "home", return_value=home):
            for executable in ("/Applications/IwaraTool.app/Contents/MacOS/IwaraTool", "main.py"):
                with mock.patch.object(paths.sys, "argv", [executable]):
                    self.assertEqual(paths.app_data_dir(), home / "Library" / "Application Support" / "IwaraTool")
                    self.assertEqual(paths.default_download_dir(), home / "Downloads" / "IwaraTool")

    def test_other_platforms_keep_portable_locations(self):
        with mock.patch.object(paths.sys, "platform", "win32"), \
                mock.patch.object(paths.sys, "argv", ["portable/IwaraTool.exe"]):
            root = Path("portable").resolve()
            self.assertEqual(paths.app_data_dir(), root / "data")
            self.assertEqual(paths.default_download_dir(), root / "download")


if __name__ == "__main__":
    unittest.main()
