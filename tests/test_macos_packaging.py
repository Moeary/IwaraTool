"""macOS bundle repair and DMG layout logic, tested without Apple tools."""
import sys
import tempfile
import unittest
from pathlib import Path

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import macos_bundle as bundle  # noqa: E402
import macos_dmg as dmg  # noqa: E402

BUILD_ENV = "/Users/runner/work/IwaraTool/IwaraTool/.pixi/envs/default"

OTOOL_L = f"""IwaraTool.app/Contents/MacOS/IwaraTool (architecture arm64):
\t{BUILD_ENV}/lib/libpython3.11.dylib (compatibility version 3.11.0, current version 3.11.0)
\t@rpath/QtCore.framework/Versions/A/QtCore (compatibility version 6.0.0, current version 6.6.2)
\t/usr/lib/libSystem.B.dylib (compatibility version 1.0.0, current version 1345.100.2)
"""

OTOOL_RPATHS = f"""Load command 12
          cmd LC_LOAD_DYLIB
      cmdsize 56
         name /usr/lib/libSystem.B.dylib (offset 24)
Load command 13
          cmd LC_RPATH
      cmdsize 72
         path {BUILD_ENV}/lib (offset 12)
Load command 14
          cmd LC_RPATH
      cmdsize 32
         path @loader_path (offset 12)
"""


class OtoolParsingTests(unittest.TestCase):
    def test_libraries_drop_headers_and_versions(self):
        self.assertEqual(
            bundle.parse_otool_libraries(OTOOL_L),
            [
                f"{BUILD_ENV}/lib/libpython3.11.dylib",
                "@rpath/QtCore.framework/Versions/A/QtCore",
                "/usr/lib/libSystem.B.dylib",
            ],
        )

    def test_rpaths_and_install_id(self):
        self.assertEqual(bundle.parse_otool_rpaths(OTOOL_RPATHS), [f"{BUILD_ENV}/lib", "@loader_path"])
        self.assertEqual(bundle.parse_otool_install_id("x.dylib:\n@rpath/x.dylib\n"), "@rpath/x.dylib")
        self.assertEqual(bundle.parse_otool_install_id("IwaraTool:\n"), "")

    def test_reference_classes(self):
        self.assertTrue(bundle.is_external_reference(f"{BUILD_ENV}/lib/libssl.3.dylib"))
        self.assertFalse(bundle.is_external_reference("/usr/lib/libSystem.B.dylib"))
        self.assertFalse(bundle.is_external_reference("/System/Library/Frameworks/AppKit.framework/AppKit"))
        self.assertFalse(bundle.is_external_reference("@executable_path/libpython3.11.dylib"))


class BundleFixPlanTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.macos = Path(self._tmp.name) / "IwaraTool.app" / "Contents" / "MacOS"
        self.executable = self._touch("IwaraTool")
        self.libpython = self._touch("libpython3.11.dylib")
        self.qtcore = self._touch("PySide6/Qt/lib/QtCore.framework/Versions/A/QtCore")
        self.index = bundle.index_bundle_files([self.executable, self.libpython, self.qtcore])

    def tearDown(self):
        self._tmp.cleanup()

    def _touch(self, relative: str) -> Path:
        path = self.macos / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\xcf\xfa\xed\xfe")
        return path

    def test_build_machine_libpython_is_pointed_at_the_bundled_copy(self):
        info = bundle.BinaryInfo(
            self.executable,
            bundle.parse_otool_libraries(OTOOL_L),
            rpaths=[f"{BUILD_ENV}/lib"],
        )
        plan = bundle.plan_fixes(info, self.macos, self.index)
        self.assertEqual(
            plan.changes,
            [(f"{BUILD_ENV}/lib/libpython3.11.dylib", "@executable_path/libpython3.11.dylib")],
        )
        self.assertEqual(plan.delete_rpaths, [f"{BUILD_ENV}/lib"])
        # QtCore was only reachable through the deleted rpath: point at the bundle instead.
        self.assertEqual(plan.add_rpaths, ["@loader_path/PySide6/Qt/lib"])
        self.assertEqual(plan.missing, [])
        args = plan.install_name_tool_args()
        self.assertEqual(args[:3], ["-change", f"{BUILD_ENV}/lib/libpython3.11.dylib", "@executable_path/libpython3.11.dylib"])
        self.assertIn("-delete_rpath", args)

    def test_dylib_install_id_and_missing_libraries(self):
        info = bundle.BinaryInfo(
            self.libpython,
            [f"{BUILD_ENV}/lib/libpython3.11.dylib", f"{BUILD_ENV}/lib/libintl.8.dylib"],
            install_id=f"{BUILD_ENV}/lib/libpython3.11.dylib",
        )
        plan = bundle.plan_fixes(info, self.macos, self.index)
        self.assertEqual(plan.new_id, "@executable_path/libpython3.11.dylib")
        self.assertEqual(plan.changes, [])
        self.assertEqual(plan.missing, [f"{BUILD_ENV}/lib/libintl.8.dylib"])

    def test_clean_binary_needs_no_changes(self):
        info = bundle.BinaryInfo(
            self.executable,
            ["@executable_path/libpython3.11.dylib", "/usr/lib/libSystem.B.dylib"],
            rpaths=["@loader_path"],
        )
        plan = bundle.plan_fixes(info, self.macos, self.index)
        self.assertEqual(plan.install_name_tool_args(), [])
        self.assertEqual(bundle.audit_binary(info, self.macos), [])

    def test_audit_reports_external_and_dangling_references(self):
        info = bundle.BinaryInfo(
            self.executable,
            [f"{BUILD_ENV}/lib/libssl.3.dylib", "@executable_path/libmissing.dylib"],
        )
        issues = bundle.audit_binary(info, self.macos)
        self.assertEqual(len(issues), 2)
        self.assertIn("build-machine path", issues[0])
        self.assertIn("does not exist", issues[1])


class LaunchCheckTests(unittest.TestCase):
    def test_dyld_print_libraries_formats(self):
        stderr = "\n".join([
            "dyld[4242]: <A1B2C3D4-0000-0000-0000-000000000000> /Applications/IwaraTool.app/Contents/MacOS/IwaraTool",
            f"dyld[4242]: <ABCD> {BUILD_ENV}/lib/libpython3.11.dylib",
            "dyld: loaded: /usr/lib/libSystem.B.dylib",
            "IwaraTool 1.1.4: Qt multimedia and bundled resources OK",
        ])
        loaded = bundle.parse_dyld_loaded_paths(stderr)
        self.assertEqual(len(loaded), 3)
        self.assertEqual(
            bundle.foreign_loaded_paths(loaded, [Path(BUILD_ENV)]),
            [f"{BUILD_ENV}/lib/libpython3.11.dylib"],
        )


class DmgLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_settings_offer_the_applications_drop_target(self):
        settings = dmg.dmg_settings(Path("/tmp/IwaraTool.app"), Path("/tmp/background.png"), None)
        self.assertEqual(settings["symlinks"], {"Applications": "/Applications"})
        self.assertEqual(set(settings["icon_locations"]), {"IwaraTool.app", "Applications"})
        self.assertEqual(settings["window_rect"][1], dmg.WINDOW_SIZE)
        self.assertNotIn("icon", settings)

    def test_background_is_rendered_at_both_scales(self):
        with tempfile.TemporaryDirectory() as tmp:
            for scale, name in ((1, "background.png"), (2, "background@2x.png")):
                path = dmg.render_background(Path(tmp) / name, scale)
                image = QImage(str(path))
                self.assertEqual(
                    (image.width(), image.height()),
                    (dmg.WINDOW_SIZE[0] * scale, dmg.WINDOW_SIZE[1] * scale),
                )


if __name__ == "__main__":
    unittest.main()
