"""Build a native macOS .app and archive it without losing bundle metadata."""
from __future__ import annotations

import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.version import __version__  # noqa: E402


def smoke_test(command: list[str]) -> None:
    with tempfile.TemporaryDirectory(prefix="iwaratool-smoke-") as home:
        subprocess.run(
            [*command, "--smoke-test"],
            env={**os.environ, "HOME": home, "QT_QPA_PLATFORM": "cocoa"},
            timeout=90, check=True,
        )


def main() -> None:
    if sys.platform != "darwin":
        raise SystemExit("This build task requires macOS.")
    arch = {"arm64": "ARM64", "x86_64": "AMD64"}.get(platform.machine().lower())
    if not arch:
        raise SystemExit(f"Unsupported macOS architecture: {platform.machine()}")
    smoke_test([sys.executable, str(ROOT / "main.py")])
    build_dir = ROOT / "build" / "macos"
    build_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        sys.executable, "-m", "nuitka",
        "--assume-yes-for-downloads", "--standalone", "--macos-create-app-bundle",
        "--static-libpython=no",
        "--macos-app-name=IwaraTool", "--macos-signed-app-name=io.github.moeary.iwaratool",
        f"--macos-app-version={__version__}", "--macos-app-icon=docs/iwaratool_logo.png",
        "--enable-plugin=pyside6", "--include-qt-plugins=multimedia",
        "--include-package-data=cloudscraper",
        "--include-data-file=app/icon.ico=app/icon.ico",
        "--include-data-file=app/data/tag_translations/loveiwara_iwara_tags_localized.json=app/data/tag_translations/loveiwara_iwara_tags_localized.json",
        "--include-data-file=app/data/oreno3d_iwara_map.json=app/data/oreno3d_iwara_map.json",
        "--output-filename=IwaraTool", "--output-folder-name=IwaraTool",
        f"--output-dir={build_dir}", "--remove-output",
        "main.py",
    ], cwd=ROOT, check=True)
    target = build_dir / "IwaraTool.app"
    smoke_test([str(target / "Contents" / "MacOS" / "IwaraTool")])
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    archive = dist / f"IwaraTool-macOS-{arch}-v{__version__}.zip"
    subprocess.run([
        "ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(target), str(archive),
    ], check=True)
    print(f"Created {archive}")


if __name__ == "__main__":
    main()
