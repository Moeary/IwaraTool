"""Which release APK the update prompt offers a phone."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts" / "tools"))


class ApkChoiceTests(unittest.TestCase):
    APKS = {
        "IwaraTool-Android-arm64-v8a-v1.3.0.apk": "https://x/arm64",
        "IwaraTool-Android-armeabi-v7a-v1.3.0.apk": "https://x/v7a",
        "IwaraTool-Android-x86_64-v1.3.0.apk": "https://x/x86_64",
        "IwaraTool-Android-universal-v1.3.0.apk": "https://x/universal",
    }

    def test_the_device_abi_first_then_universal(self):
        from app.presentation.updates import apk_for

        self.assertEqual(apk_for(self.APKS, ["arm64-v8a", "armeabi-v7a", "armeabi"]), "https://x/arm64")
        self.assertEqual(apk_for(self.APKS, ["armeabi-v7a", "armeabi"]), "https://x/v7a")
        self.assertEqual(apk_for(self.APKS, ["mips"]), "https://x/universal")
        self.assertEqual(apk_for(self.APKS, []), "https://x/universal")
        self.assertEqual(apk_for({}, ["arm64-v8a"]), "")

    def test_build_names_each_apk_by_its_abi(self):
        import build

        self.assertEqual(build.apk_abi(Path("app-arm64-v8a-release.apk")), "arm64-v8a")
        self.assertEqual(build.apk_abi(Path("app-armeabi-v7a-release.apk")), "armeabi-v7a")
        self.assertEqual(build.apk_abi(Path("app-x86_64-release.apk")), "x86_64")
        self.assertEqual(build.apk_abi(Path("app-release.apk")), "universal")


if __name__ == "__main__":
    unittest.main()
