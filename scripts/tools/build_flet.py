"""Build the Flet interface with ``flet build`` from a clean staging copy.

    pixi run -e flet build-flet                          # this host's desktop platform
    pixi run -e flet build-flet --label v1.3.0-alpha1    # name used in the artifact
    pixi run -e flet build-flet --no-smoke               # skip launching the result
    pixi run -e flet build-flet --platform apk           # Android (needs the Android SDK; CI builds it)
    pixi run -e flet build-flet --install                # macOS: also replace the one test copy, /Applications/IwaraTool Flet.app

Steps (docs/flet_ui_plan.md 2.5):

1. Stage only what the Flet app needs in build/flet-src: main_flet.py, the
   top-level app modules, app/core, app/presentation, app/ui_flet, app/data
   and the packaging files.  Never app/ui (the PySide6 interface).
2. Write app/version.py into the staged pyproject.toml.
3. Run ``flet build <platform>`` there.  Flet keeps its Flutter project in
   build/flet-src/build, apart from the Nuitka build in build/macos.
4. Fail if the result contains PySide6, shiboken6, qfluentwidgets or app/ui.
5. Launch the result in smoke-test mode (data in a temporary folder) and check
   its report: it started, knows it is packaged, loaded no Qt module, and the
   app storage Flet provides (the data folder of real runs) is outside the bundle.
   Desktop only: an APK needs a device or an emulator.
6. Zip it as dist/IwaraTool-Flet-<Platform>-<arch>-<label>.zip (an APK is
   copied as dist/IwaraTool-Flet-Android-<label>.apk).
7. macOS: forget the build's own bundles in Launch Services (so Spotlight, Launchpad
   and "Open With" do not list a copy per build folder) and, with --install, replace
   the single test copy at /Applications/IwaraTool Flet.app (the release IwaraTool.app
   is left alone).
"""
from __future__ import annotations

import argparse
import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STAGE = ROOT / "build" / "flet-src"
OUTPUT = ROOT / "build" / "flet-out"
DIST = ROOT / "dist"
PACKAGING = ROOT / "packaging" / "flet"

APP_PACKAGES = ("core", "presentation", "ui_flet", "data")
FORBIDDEN = re.compile(r"(^|[/\\])(PySide6|shiboken6|qfluentwidgets|qframelesswindow)([/\\.]|$)|(^|[/\\])app[/\\]ui[/\\]")
PLATFORM_LABELS = {"macos": "macOS", "windows": "Windows", "linux": "Linux", "apk": "Android"}
DESKTOP_TARGETS = frozenset({"macos", "windows", "linux"})
TEST_INSTALL = Path("/Applications/IwaraTool Flet.app")  # the one test copy on this Mac
LSREGISTER = "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
SMOKE_TIMEOUT = 240  # start-up plus the desktop check (each of its steps is capped)


def host_platform() -> str:
    if sys.platform == "darwin":
        return "macos"
    if sys.platform == "win32":
        return "windows"
    return "linux"


def arch_label(target: str) -> str:
    machine = platform.machine().lower()
    if target == "macos":
        return "AppleSilicon-ARM64" if machine in ("arm64", "aarch64") else "Intel-x86_64"
    return {"amd64": "AMD64", "x86_64": "AMD64", "arm64": "ARM64", "aarch64": "ARM64"}.get(machine, machine)


def app_version() -> str:
    text = (ROOT / "app" / "version.py").read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    if not match:
        raise SystemExit("app/version.py has no __version__")
    return match.group(1)


def _ignore(_dir: str, names: list[str]) -> set[str]:
    return {name for name in names if name == "__pycache__" or name.endswith((".pyc", ".pyo"))}


def stage(version: str) -> None:
    if STAGE.exists():
        shutil.rmtree(STAGE)
    (STAGE / "app").mkdir(parents=True)
    shutil.copy2(ROOT / "main_flet.py", STAGE / "main_flet.py")
    for module in (ROOT / "app").glob("*.py"):
        shutil.copy2(module, STAGE / "app" / module.name)
    shutil.copy2(ROOT / "app" / "icon.ico", STAGE / "app" / "icon.ico")
    for package in APP_PACKAGES:
        shutil.copytree(ROOT / "app" / package, STAGE / "app" / package, ignore=_ignore)
    shutil.copytree(PACKAGING / "assets", STAGE / "assets")
    pyproject = (PACKAGING / "pyproject.toml").read_text(encoding="utf-8")
    pyproject, count = re.subn(r'(?m)^version = "[^"]*"', f'version = "{version}"', pyproject, count=1)
    if count != 1:
        raise SystemExit("packaging/flet/pyproject.toml has no version line")
    (STAGE / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    staged = [p for p in STAGE.rglob("*") if p.is_file()]
    bad = [p for p in staged if FORBIDDEN.search(p.relative_to(STAGE).as_posix())]
    if bad:
        raise SystemExit(f"staging picked up old-interface files: {bad[:5]}")
    print(f"Staged {len(staged)} files in {STAGE.relative_to(ROOT)}")


def flet_build(target: str, version: str, verbose: bool) -> Path:
    out = OUTPUT / target
    if out.exists():
        shutil.rmtree(out)
    command = [
        sys.executable, "-m", "flet_cli.cli", "build", target,
        "--yes", "--no-rich-output",
        "--python-version", "3.12",
        "--build-version", re.sub(r"[^0-9.].*$", "", version) or "0.0.0",
        "--build-number", str(build_number(version)),
        "--output", str(out),
    ]
    if verbose:
        command.append("-v")
    print("Running:", " ".join(command[2:]))
    subprocess.run(command, cwd=STAGE, check=True)
    return out


def build_number(version: str) -> int:
    """1.3.0 → 10300: Android's versionCode must grow with every release."""
    parts = [int(p) for p in re.findall(r"\d+", re.sub(r"[^0-9.].*$", "", version))[:3]]
    parts += [0] * (3 - len(parts))
    return max(1, parts[0] * 10000 + parts[1] * 100 + parts[2])


def find_executable(target: str, out: Path) -> Path:
    if target == "macos":
        apps = sorted(out.glob("*.app"))
        if not apps:
            raise SystemExit(f"no .app in {out}")
        return apps[0] / "Contents" / "MacOS" / apps[0].stem
    if target == "windows":
        exe = out / "IwaraTool.exe"
        if not exe.exists():
            candidates = sorted(out.glob("*.exe"))
            if not candidates:
                raise SystemExit(f"no .exe in {out}")
            exe = candidates[0]
        return exe
    binary = out / "IwaraTool"
    if not binary.exists():
        raise SystemExit(f"no IwaraTool binary in {out}")
    return binary


def audit(out: Path) -> None:
    """No Qt and no old interface anywhere, including inside zipped app archives."""

    problems: list[str] = []
    for path in out.rglob("*"):
        rel = path.relative_to(out).as_posix()
        if FORBIDDEN.search(rel):
            problems.append(rel)
        if path.is_file() and path.suffix in (".zip", ".apk", ".aab"):
            with zipfile.ZipFile(path) as archive:
                problems += _archive_problems(archive, rel)
    if problems:
        raise SystemExit("Flet build contains old-interface files:\n  " + "\n  ".join(problems[:20]))
    print("Audit: no PySide6, shiboken6, qfluentwidgets or app/ui in the build")


def _archive_problems(archive: zipfile.ZipFile, label: str) -> list[str]:
    """Forbidden names in an archive and in the archives inside it (an APK holds the app as a zip)."""
    problems = [f"{label}!{name}" for name in archive.namelist() if FORBIDDEN.search(name)]
    for name in archive.namelist():
        if name.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(archive.read(name))) as inner:
                problems += _archive_problems(inner, f"{label}!{name}")
    return problems


def smoke(executable: Path, out: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="iwaratool-flet-smoke-") as tmp:
        report_path = Path(tmp) / "report.json"
        data_dir = Path(tmp) / "data"
        # Settings and history go to the temporary folder, never the user's;
        # the storage folder Flet would give the app is only checked in the report.
        env = dict(
            os.environ,
            IWARATOOL_SMOKE_TEST="1",
            IWARATOOL_SMOKE_REPORT=str(report_path),
            IWARATOOL_SMOKE_DESKTOP="1",  # close to tray / Dock, login item, notification (desktop_check.py)
            IWARATOOL_DATA_DIR=str(data_dir),
            IWARATOOL_DOWNLOAD_DIR=str(Path(tmp) / "downloads"),
        )
        env.pop("FLET_PLATFORM", None)
        process = subprocess.Popen([str(executable)], env=env, cwd=tmp)
        deadline = time.monotonic() + SMOKE_TIMEOUT
        try:
            while time.monotonic() < deadline and not report_path.exists():
                if process.poll() is not None and not report_path.exists():
                    raise SystemExit(f"smoke test: the app exited ({process.returncode}) without a report")
                time.sleep(0.5)
            if not report_path.exists():
                raise SystemExit(f"smoke test: no report within {SMOKE_TIMEOUT}s")
            time.sleep(0.5)  # let the writer finish
            report = json.loads(report_path.read_text(encoding="utf-8"))
            reported_data = Path(report.get("data_dir", "")).resolve()
            if reported_data != data_dir.resolve():
                raise SystemExit(f"smoke test: the app used {reported_data}, not {data_dir}")
        finally:
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
    print("Smoke report:", json.dumps(report, ensure_ascii=False))
    if report.get("qt_modules"):
        raise SystemExit(f"smoke test: Qt modules loaded: {report['qt_modules']}")
    desktop = report.get("desktop")
    if desktop is not None and str(desktop.get("window_check", "")).startswith("inconclusive"):
        print(f"WARNING: close-to-tray / Dock not verified on this machine ({desktop['window_check']})")
    if desktop is not None and desktop.get("failures"):
        raise SystemExit(f"smoke test: desktop check failed: {desktop['failures']}")
    if not report.get("packaged"):
        raise SystemExit("smoke test: the app does not know it is packaged (FLET_PLATFORM unset)")
    storage = Path(report.get("flet_storage", ""))
    if not report.get("flet_storage") or not storage.is_absolute() or out.resolve() in storage.resolve().parents:
        raise SystemExit(f"smoke test: Flet app storage {storage} is missing or inside the bundle")


def package(target: str, out: Path, label: str) -> Path:
    DIST.mkdir(exist_ok=True)
    if target == "apk":
        apks = sorted(out.rglob("*.apk"))
        if not apks:
            raise SystemExit(f"no .apk in {out}")
        for apk in apks:  # one universal APK, or one per ABI with split_per_abi
            suffix = "" if len(apks) == 1 else "-" + apk.stem.replace("app-", "").replace("-release", "")
            target_path = DIST / f"IwaraTool-Flet-Android{suffix}-{label}.apk"
            shutil.copy2(apk, target_path)
            print(f"Artifact: {target_path.relative_to(ROOT)} ({target_path.stat().st_size / 1e6:.1f} MB)")
        return target_path
    archive = DIST / f"IwaraTool-Flet-{PLATFORM_LABELS[target]}-{arch_label(target)}-{label}.zip"
    archive.unlink(missing_ok=True)
    if target == "macos":
        app = sorted(out.glob("*.app"))[0]
        # ditto keeps the bundle's symlinks, permissions and signature intact
        subprocess.run(["ditto", "-c", "-k", "--keepParent", str(app), str(archive)], check=True)
    else:
        shutil.make_archive(str(archive.with_suffix("")), "zip", root_dir=out.parent, base_dir=out.name)
    print(f"Artifact: {archive.relative_to(ROOT)} ({archive.stat().st_size / 1e6:.1f} MB)")
    return archive


def forget_build_bundles(out: Path) -> None:
    """Launch Services registers every .app a build writes; only the installed copy should be listed."""
    bundles = [*out.glob("*.app"), *(STAGE / "build").rglob("Release/*.app")]
    for bundle in bundles:
        subprocess.run([LSREGISTER, "-u", str(bundle)], check=False, capture_output=True)


def install(out: Path) -> None:
    """Replace /Applications/IwaraTool Flet.app with this build (quits the running copy first)."""
    app = sorted(out.glob("*.app"))[0]
    subprocess.run(["pkill", "-f", f"{TEST_INSTALL}/Contents/MacOS/"], check=False)
    if TEST_INSTALL.exists():
        shutil.rmtree(TEST_INSTALL)
    subprocess.run(["ditto", str(app), str(TEST_INSTALL)], check=True)
    subprocess.run([LSREGISTER, "-f", str(TEST_INSTALL)], check=False, capture_output=True)
    print(f"Installed: {TEST_INSTALL}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--platform", choices=sorted(PLATFORM_LABELS), default=host_platform())
    parser.add_argument("--label", help="version label in the artifact name (default: v<app version>)")
    parser.add_argument("--no-smoke", action="store_true", help="do not launch the built app")
    parser.add_argument("--install", action="store_true", help=f"macOS: replace the test copy at {TEST_INSTALL}")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    if args.platform in DESKTOP_TARGETS and args.platform != host_platform():
        raise SystemExit(f"{args.platform} can only be built on {args.platform}; this host is {host_platform()}")

    version = app_version()
    stage(version)
    out = flet_build(args.platform, version, args.verbose)
    audit(out)
    if not args.no_smoke and args.platform in DESKTOP_TARGETS:
        smoke(find_executable(args.platform, out), out)
    package(args.platform, out, args.label or f"v{version}")
    if args.platform == "macos":
        forget_build_bundles(out)
        if args.install:
            install(out)


if __name__ == "__main__":
    main()
