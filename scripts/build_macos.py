"""Build a native macOS .app, make it self-contained, and ship it as DMG + zip.

Steps: smoke-test the sources, build with Nuitka, rewrite library references
that still point into the build machine's Python environment, re-sign
(ad-hoc), audit every Mach-O file, launch the bundle while watching which
libraries dyld loads, then package a drag-to-Applications DMG and launch the
app again from the mounted image.
"""
from __future__ import annotations

import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from app.version import __version__  # noqa: E402
from macos_bundle import foreign_loaded_paths, make_self_contained, parse_dyld_loaded_paths  # noqa: E402
from macos_dmg import build_dmg, mounted, verify_dmg_layout  # noqa: E402

# Release asset labels: what a Mac user reads in "About This Mac".
ARCH_LABELS = {"arm64": "AppleSilicon-ARM64", "x86_64": "Intel-x86_64"}


def smoke_test(command: list[str], *, forbid_build_env: bool = False) -> None:
    """Launch in smoke-test mode; optionally fail when a library comes from the build env."""

    env = {"QT_QPA_PLATFORM": "cocoa"}
    if forbid_build_env:
        env["DYLD_PRINT_LIBRARIES"] = "1"
    with tempfile.TemporaryDirectory(prefix="iwaratool-smoke-") as home:
        result = subprocess.run(
            [*command, "--smoke-test", "--smoke-media", str(ROOT / "tests" / "fixtures" / "video-smoke.mp4")],
            env={**os.environ, "HOME": home, **env},
            timeout=120, text=True, capture_output=True,
        )
    print(result.stdout, end="")
    if result.returncode != 0:
        print(result.stderr, end="", file=sys.stderr)
        raise SystemExit(f"Smoke test failed ({result.returncode}): {' '.join(command)}")
    if not forbid_build_env:
        return
    loaded = parse_dyld_loaded_paths(result.stderr)
    if not loaded:
        print("warning: dyld printed no library list; relying on the static audit only")
        return
    foreign = foreign_loaded_paths(loaded, [ROOT, Path(sys.prefix).resolve()])
    if foreign:
        raise SystemExit("The bundle loaded libraries from the build environment:\n" + "\n".join(foreign))
    print(f"dyld loaded {len(loaded)} images, none from the build environment.")


def build_icon(build_dir: Path) -> Path:
    """Use Apple's tools to preserve all icon sizes without extra Python packages."""
    iconset = build_dir / "IwaraTool.iconset"
    iconset.mkdir(exist_ok=True)
    source = ROOT / "docs" / "iwaratool_logo.png"
    for size in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            suffix = "@2x" if scale == 2 else ""
            subprocess.run([
                "/usr/bin/sips", "--resampleHeightWidth", str(size * scale), str(size * scale),
                str(source), "--out", str(iconset / f"icon_{size}x{size}{suffix}.png"),
            ], check=True, capture_output=True)
    icon = build_dir / "IwaraTool.icns"
    subprocess.run(["/usr/bin/iconutil", "-c", "icns", str(iconset), "-o", str(icon)], check=True)
    return icon


def nuitka_build(build_dir: Path, icon: Path) -> Path:
    subprocess.run([
        sys.executable, "-m", "nuitka",
        "--assume-yes-for-downloads", "--standalone", "--macos-create-app-bundle",
        "--static-libpython=no",
        "--macos-app-name=IwaraTool", "--macos-signed-app-name=io.github.moeary.iwaratool",
        f"--macos-app-version={__version__}", f"--macos-app-icon={icon}",
        "--enable-plugin=pyside6", "--include-qt-plugins=multimedia",
        "--include-package-data=cloudscraper",
        "--include-data-file=app/icon.ico=app/icon.ico",
        "--include-data-file=app/data/tag_translations/loveiwara_iwara_tags_localized.json=app/data/tag_translations/loveiwara_iwara_tags_localized.json",
        "--include-data-file=app/data/oreno3d_iwara_map.json=app/data/oreno3d_iwara_map.json",
        "--output-filename=IwaraTool", "--output-folder-name=IwaraTool",
        f"--output-dir={build_dir}", "--remove-output",
        "main.py",
    ], cwd=ROOT, check=True)
    return build_dir / "IwaraTool.app"


def main() -> None:
    if sys.platform != "darwin":
        raise SystemExit("This build task requires macOS.")
    machine = platform.machine().lower()
    arch = ARCH_LABELS.get(machine)
    if not arch:
        raise SystemExit(f"Unsupported macOS architecture: {platform.machine()}")
    smoke_test([sys.executable, str(ROOT / "main.py")])
    build_dir = ROOT / "build" / "macos"
    build_dir.mkdir(parents=True, exist_ok=True)
    icon = build_icon(build_dir)
    target = nuitka_build(build_dir, icon)

    make_self_contained(target)
    executable = target / "Contents" / "MacOS" / "IwaraTool"
    smoke_test([str(executable)], forbid_build_env=True)

    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    stem = f"IwaraTool-macOS-{arch}-v{__version__}"
    dmg = build_dmg(target, dist / f"{stem}.dmg", work_dir=build_dir, icon=icon)
    with mounted(dmg) as volume:
        issues = verify_dmg_layout(volume, target.name)
        if issues:
            raise SystemExit("DMG check failed: " + "; ".join(issues))
        smoke_test([str(volume / target.name / "Contents" / "MacOS" / "IwaraTool")], forbid_build_env=True)
    print(f"{dmg.name}: layout and launch from the mounted image OK")

    archive = dist / f"{stem}.zip"
    subprocess.run([
        "ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(target), str(archive),
    ], check=True)
    print(f"Created {archive}")


if __name__ == "__main__":
    main()
