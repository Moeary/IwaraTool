from __future__ import annotations

import hashlib
import os
import tempfile
import unittest

from app.core import self_update
from app.core.self_update import UpdateAsset, UpdateError

PAYLOAD = b"new build bytes" * 1000
DIGEST = hashlib.sha256(PAYLOAD).hexdigest()


class _Response:
    def __init__(self, body, headers=None):
        self._body = body
        self.headers = headers or {"Content-Length": str(len(body))}
        self.text = body.decode("latin-1") if isinstance(body, bytes) else body

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size=1):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i + chunk_size]

    def close(self):
        pass


class _Session:
    def __init__(self, body):
        self.body = body

    def get(self, url, **kwargs):
        return _Response(self.body)


class AssetSelectionTests(unittest.TestCase):
    ASSETS = [
        {"name": "IwaraTool-Linux-AMD64-v1.0.0", "browser_download_url": "https://x/linux"},
        {"name": "IwaraTool-Windows-AMD64-v1.0.0.exe", "browser_download_url": "https://x/win", "digest": f"sha256:{DIGEST}", "size": 5},
        {"name": "IwaraTool-Windows-AMD64-v1.0.0.exe.sha256", "browser_download_url": "https://x/win.sha256"},
    ]

    def test_picks_platform_binary_not_checksum(self):
        asset = self_update.pick_asset(self.ASSETS, platform="win32")
        self.assertEqual(asset["name"], "IwaraTool-Windows-AMD64-v1.0.0.exe")
        self.assertEqual(self_update.pick_asset(self.ASSETS, platform="linux")["name"], "IwaraTool-Linux-AMD64-v1.0.0")
        self.assertIsNone(self_update.pick_asset(self.ASSETS, platform="darwin"))

    def test_digest_field_wins(self):
        resolved = self_update.resolve_update_asset(self.ASSETS, platform="win32")
        self.assertEqual(resolved.sha256, DIGEST)
        self.assertEqual(resolved.url, "https://x/win")

    def test_falls_back_to_checksum_file(self):
        assets = [dict(self.ASSETS[1]), self.ASSETS[2]]
        del assets[0]["digest"]
        resolved = self_update.resolve_update_asset(
            assets,
            fetch_text=lambda url: f"{DIGEST.upper()}  IwaraTool-Windows-AMD64-v1.0.0.exe\n",
            platform="win32",
        )
        self.assertEqual(resolved.sha256, DIGEST)

    def test_no_checksum_available(self):
        assets = [dict(self.ASSETS[1])]
        del assets[0]["digest"]
        resolved = self_update.resolve_update_asset(assets, fetch_text=lambda url: "", platform="win32")
        self.assertEqual(resolved.sha256, "")

    def test_checksum_text_parsing(self):
        text = f"{'a' * 64}  other.exe\n{DIGEST}  target.exe\n"
        self.assertEqual(self_update.parse_checksum_text(text, "target.exe"), DIGEST)
        self.assertEqual(self_update.parse_checksum_text(DIGEST, "anything"), DIGEST)
        self.assertEqual(self_update.parse_checksum_text(text, "missing.exe"), "")


class DownloadTests(unittest.TestCase):
    def test_verified_download_installs_file(self):
        with tempfile.TemporaryDirectory() as root:
            dest = os.path.join(root, "updates", "IwaraTool.exe")
            seen = []
            asset = UpdateAsset("IwaraTool.exe", "https://x/win", DIGEST)
            path = self_update.download_verified(
                asset, dest, session=_Session(PAYLOAD), progress=lambda d, t: seen.append((d, t))
            )
            self.assertEqual(path, dest)
            with open(dest, "rb") as fh:
                self.assertEqual(fh.read(), PAYLOAD)
            self.assertFalse(os.path.exists(dest + ".part"))
            self.assertEqual(seen[-1], (len(PAYLOAD), len(PAYLOAD)))

    def test_mismatch_leaves_nothing_behind(self):
        with tempfile.TemporaryDirectory() as root:
            dest = os.path.join(root, "IwaraTool.exe")
            asset = UpdateAsset("IwaraTool.exe", "https://x/win", DIGEST)
            with self.assertRaises(UpdateError):
                self_update.download_verified(asset, dest, session=_Session(PAYLOAD + b"tampered"))
            self.assertEqual(os.listdir(root), [])

    def test_refuses_without_checksum_or_url(self):
        with tempfile.TemporaryDirectory() as root:
            dest = os.path.join(root, "a.exe")
            with self.assertRaises(UpdateError):
                self_update.download_verified(UpdateAsset("a", "https://x", ""), dest, session=_Session(b""))
            with self.assertRaises(UpdateError):
                self_update.download_verified(UpdateAsset("a", "", DIGEST), dest, session=_Session(b""))

    def test_cancel_removes_partial_file(self):
        with tempfile.TemporaryDirectory() as root:
            dest = os.path.join(root, "a.exe")
            asset = UpdateAsset("a", "https://x", DIGEST)
            with self.assertRaises(UpdateError):
                self_update.download_verified(asset, dest, session=_Session(PAYLOAD), cancelled=lambda: True)
            self.assertEqual(os.listdir(root), [])


class SwapScriptTests(unittest.TestCase):
    def test_script_waits_swaps_and_relaunches(self):
        script = self_update.build_swap_script(pid=4242, new_path=r"C:\d\new.exe", target_path=r"C:\app\IwaraTool.exe")
        self.assertIn('PID eq 4242', script)
        self.assertIn(r'move /Y "C:\d\new.exe" "C:\app\IwaraTool.exe"', script)
        self.assertIn(r'start "" "C:\app\IwaraTool.exe"', script)
        self.assertLess(script.index("tasklist"), script.index("move /Y"))
        self.assertLess(script.index("move /Y"), script.index("start"))

    def test_source_checkout_cannot_self_update(self):
        self.assertFalse(self_update.can_self_update())


if __name__ == "__main__":
    unittest.main()
