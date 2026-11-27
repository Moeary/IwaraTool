"""Build IwaraTool with ``flet build`` from a clean staging copy.

    pixi run build                          # this host's desktop platform
    pixi run build --label v1.3.0-alpha1    # name used in the artifact
    pixi run build --no-smoke               # skip launching the result
    pixi run build --platform apk           # Android (needs the Android SDK; CI builds it)
    pixi run build --install                # macOS: also replace the one test copy, /Applications/IwaraTool Dev.app

Steps:

1. Stage only what the app needs in build/flet-src: main.py, the top-level
   app modules, app/core, app/presentation, app/ui, app/data, and the icon
   (app/icon.png) as assets/icon.png.
2. Write pyproject.toml there from ``PYPROJECT`` with app/version.py's version.
3. Run ``flet build <platform>`` there.  Flet keeps its Flutter project in
   build/flet-src/build.
4. Fail if the result contains PySide6, shiboken6, qfluentwidgets or
   qframelesswindow (v1.2's toolkit, never needed again).
5. Launch the result in smoke-test mode (data in a temporary folder) and check
   its report: it started, knows it is packaged, and the app storage Flet
   provides is outside the bundle.  Desktop only: an APK needs a device or an
   emulator.
6. Android: check who signed the APK.  Android installs an update over an
   installed copy only when both carry the same certificate, so releases are
   signed with one long-lived key (``FLET_ANDROID_SIGNING_KEY_STORE``,
   ``FLET_ANDROID_SIGNING_KEY_STORE_PASSWORD``, ``FLET_ANDROID_SIGNING_KEY_ALIAS``;
   without them Flet signs with the machine's debug key).  The certificate's
   SHA-256 must match ``RELEASE_CERT_SHA256``; ``--require-signing``
   (tags in CI) also refuses a build without the release key.
7. Zip it as dist/IwaraTool-<Platform>-<arch>-<label>.zip.  Android is built
   twice: one APK per ABI (--split-per-abi) and a universal one, copied as
   dist/IwaraTool-Android-<arm64-v8a|armeabi-v7a|x86_64|universal>-<label>.apk.
8. macOS: forget the build's own bundles in Launch Services (so Spotlight, Launchpad
   and "Open With" do not list a copy per build folder) and, with --install, replace
   the single test copy at /Applications/IwaraTool Dev.app (the release IwaraTool.app
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
ICON = ROOT / "app" / "icon.png"  # 1024 px; flet build derives every platform's icon from it

APP_PACKAGES = ("core", "presentation", "ui", "data")
FORBIDDEN = re.compile(r"(^|[/\\])(PySide6|shiboken6|qfluentwidgets|qframelesswindow)([/\\.]|$)")
# SHA-256 of the certificate every release APK is signed with (keytool -list -v,
# apksigner verify --print-certs).  Android installs an update only over a copy
# signed by the same certificate, so this never changes; an APK signed by another
# key is refused.  The key itself stays out of the repository (CI secrets
# ANDROID_KEYSTORE_BASE64 / ANDROID_KEYSTORE_PASSWORD / ANDROID_KEY_ALIAS).
RELEASE_CERT_SHA256 = "F2:AE:98:97:8F:4F:29:A5:EA:F7:41:1A:EE:5A:9B:35:66:0F:48:7A:43:E6:7A:7D:AA:D2:05:89:C8:B7:65:8E"

# Packaging metadata for ``flet build`` only, written into the staging folder;
# development uses pixi.  ``flet build`` fetches the dependencies from PyPI for
# the target platform.
PYPROJECT = """\
[project]
name = "iwaratool"
version = "{version}"
description = "Iwara browser and downloader"
requires-python = ">=3.12,<3.13"
dependencies = [
  "flet==1.1.3",
  "flet-video==1.1.3",
  "flet-permission-handler==1.1.3",
  "requests>=2.32.5,<3",
  "cloudscraper>=1.2.71",
]

[tool.flet]
product = "IwaraTool"
artifact = "IwaraTool"
org = "io.github.moeary"
company = "Moeary"
copyright = "Copyright (c) 2026 Moeary. MIT License."

[tool.flet.app]
path = "."
module = "main"

# Pure-Python packages ship inside a zip on Android; cloudscraper opens its
# user_agent/browsers.json by file path, so it must be unpacked.
[tool.flet.android]
extract_packages = ["cloudscraper"]

# Downloads and the picture cache live in the shared Download/IwaraTool folder
# (app/ui/platform/storage.py): All files access on Android 11+, the storage
# permissions before that (legacy storage on Android 10).
[tool.flet.android.permission]
"android.permission.MANAGE_EXTERNAL_STORAGE" = true
"android.permission.READ_EXTERNAL_STORAGE" = {{ maxSdkVersion = "32" }}
"android.permission.WRITE_EXTERNAL_STORAGE" = {{ maxSdkVersion = "29" }}

# Remote videos play through the loopback stream proxy at http://127.0.0.1,
# which Android refuses unless cleartext traffic is allowed.
[tool.flet.android.manifest_application]
usesCleartextTraffic = true
requestLegacyExternalStorage = true
"""
# An APK per ABI keeps the download small (Python and its libraries are native
# code); the universal one holds them all, for when the device's ABI is unknown.
ANDROID_ABIS = ("arm64-v8a", "armeabi-v7a", "x86_64")
PLATFORM_LABELS = {"macos": "macOS", "windows": "Windows", "linux": "Linux", "apk": "Android"}
DESKTOP_TARGETS = frozenset({"macos", "windows", "linux"})
TEST_INSTALL = Path("/Applications/IwaraTool Dev.app")  # the one test copy on this Mac
OLD_TEST_INSTALLS = (Path("/Applications/IwaraTool Flet.app"),)  # earlier names of that copy, removed on install
LSREGISTER = "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
BUILD_ATTEMPTS = 2
BUILD_RETRY_WAIT = 90
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
    shutil.copy2(ROOT / "main.py", STAGE / "main.py")
    for module in (ROOT / "app").glob("*.py"):
        shutil.copy2(module, STAGE / "app" / module.name)
    shutil.copy2(ROOT / "app" / "icon.ico", STAGE / "app" / "icon.ico")
    for package in APP_PACKAGES:
        shutil.copytree(ROOT / "app" / package, STAGE / "app" / package, ignore=_ignore)
    (STAGE / "assets").mkdir()
    shutil.copy2(ICON, STAGE / "assets" / "icon.png")
    (STAGE / "pyproject.toml").write_text(PYPROJECT.format(version=version), encoding="utf-8")
    staged = [p for p in STAGE.rglob("*") if p.is_file()]
    bad = [p for p in staged if FORBIDDEN.search(p.relative_to(STAGE).as_posix())]
    if bad:
        raise SystemExit(f"staging picked up Qt files: {bad[:5]}")
    print(f"Staged {len(staged)} files in {STAGE.relative_to(ROOT)}")


def flet_build(target: str, version: str, verbose: bool, extra: tuple[str, ...] = (), name: str = "") -> Path:
    out = OUTPUT / (name or target)
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
    command += extra
    if verbose:
        command.append("-v")
    print("Running:", " ".join(command[2:]))
    for attempt in range(1, BUILD_ATTEMPTS + 1):
        try:
            subprocess.run(command, cwd=STAGE, check=True)
            return out
        except subprocess.CalledProcessError:
            if attempt == BUILD_ATTEMPTS:
                raise
            # Gradle and pip fetch from servers that drop or rate-limit CI runners now and then.
            print(f"flet build failed (attempt {attempt}); retrying in {BUILD_RETRY_WAIT} s")
            time.sleep(BUILD_RETRY_WAIT)
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
    """No Qt anywhere, including inside zipped app archives."""

    problems: list[str] = []
    for path in out.rglob("*"):
        rel = path.relative_to(out).as_posix()
        if FORBIDDEN.search(rel):
            problems.append(rel)
        if path.is_file() and path.suffix in (".zip", ".apk", ".aab"):
            with zipfile.ZipFile(path) as archive:
                problems += _archive_problems(archive, rel)
    if problems:
        raise SystemExit("the build contains Qt files:\n  " + "\n  ".join(problems[:20]))
    print("Audit: no PySide6, shiboken6, qfluentwidgets or qframelesswindow in the build")


def _archive_problems(archive: zipfile.ZipFile, label: str) -> list[str]:
    """Forbidden names in an archive and in the archives inside it (an APK holds the app as a zip)."""
    problems = [f"{label}!{name}" for name in archive.namelist() if FORBIDDEN.search(name)]
    for name in archive.namelist():
        if name.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(archive.read(name))) as inner:
                problems += _archive_problems(inner, f"{label}!{name}")
    return problems


def smoke(executable: Path, out: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="iwaratool-smoke-") as tmp:
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


def signing_configured(environ=os.environ) -> bool:
    return bool(environ.get("FLET_ANDROID_SIGNING_KEY_STORE"))


def check_signing(require: bool) -> None:
    """Before building: the release key store is there when it must be."""
    store = os.environ.get("FLET_ANDROID_SIGNING_KEY_STORE", "")
    if store and not Path(store).is_file():
        raise SystemExit(f"FLET_ANDROID_SIGNING_KEY_STORE={store} is not a file")
    if store and not (os.environ.get("FLET_ANDROID_SIGNING_KEY_STORE_PASSWORD")
                      or os.environ.get("FLET_ANDROID_SIGNING_KEY_PASSWORD")):
        raise SystemExit("FLET_ANDROID_SIGNING_KEY_STORE is set without FLET_ANDROID_SIGNING_KEY_STORE_PASSWORD")
    if require and not store:
        raise SystemExit("--require-signing: set FLET_ANDROID_SIGNING_KEY_STORE and its password "
                         "(a debug-signed APK cannot update an installed release)")
    if not store:
        print("WARNING: no Android release key; the APK is signed with this machine's debug key "
              "and cannot update a release install")


def pinned_certificate() -> str:
    return normalize_digest(RELEASE_CERT_SHA256)


def normalize_digest(text: str) -> str:
    return re.sub(r"[^0-9a-f]", "", text.lower())


def apksigner() -> str:
    found = shutil.which("apksigner")
    if found:
        return found
    for variable in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        sdk = os.environ.get(variable)
        if sdk:
            tools = sorted((Path(sdk) / "build-tools").glob("*/apksigner*"), key=lambda p: p.parent.name)
            if tools:
                return str(tools[-1])
    return ""


def apk_certificates(apk: Path) -> list[str]:
    """SHA-256 digests of the APK's signer certificates (``apksigner verify --print-certs``)."""
    tool = apksigner()
    if not tool:
        return []
    done = subprocess.run([tool, "verify", "--print-certs", str(apk)], capture_output=True, text=True)
    if done.returncode != 0:
        raise SystemExit(f"apksigner rejected {apk.name}:\n{done.stdout}{done.stderr}")
    return [normalize_digest(m) for m in re.findall(r"certificate SHA-256 digest:\s*([0-9a-fA-F:]+)", done.stdout)]


def verify_apk_signature(out: Path, require: bool) -> None:
    """Every APK is signed by the pinned release certificate (when signed with the release key)."""
    pin = pinned_certificate()
    for apk in sorted(out.rglob("*.apk")):
        digests = apk_certificates(apk)
        if not digests:
            if require:
                raise SystemExit("--require-signing: apksigner not found (Android SDK build-tools), cannot check the APK")
            print(f"WARNING: apksigner not found; the signature of {apk.name} is not checked")
            continue
        print(f"{apk.name}: signer SHA-256 {', '.join(digests)}")
        if not signing_configured():
            continue  # a debug build: nothing to compare
        if not pin:
            raise SystemExit(f"RELEASE_CERT_SHA256 in scripts/tools/build.py is empty; set it to {digests[0]}")
        if digests != [pin]:
            raise SystemExit(f"{apk.name} is signed by {digests}, not the release certificate {pin} "
                             "(RELEASE_CERT_SHA256); installed copies could not update to it")


def apk_abi(apk: Path) -> str:
    """The ABI in a split APK's name (``app-arm64-v8a-release.apk``), else ``universal``."""
    return next((abi for abi in ANDROID_ABIS if abi in apk.name), "universal")


def package(target: str, out: Path, label: str) -> Path:
    DIST.mkdir(exist_ok=True)
    if target == "apk":
        apks = sorted(out.rglob("*.apk"))
        if not apks:
            raise SystemExit(f"no .apk in {out}")
        for apk in apks:  # one per ABI (--split-per-abi), or the universal one
            target_path = DIST / f"IwaraTool-Android-{apk_abi(apk)}-{label}.apk"
            shutil.copy2(apk, target_path)
            print(f"Artifact: {target_path.relative_to(ROOT)} ({target_path.stat().st_size / 1e6:.1f} MB)")
        return target_path
    archive = DIST / f"IwaraTool-{PLATFORM_LABELS[target]}-{arch_label(target)}-{label}.zip"
    archive.unlink(missing_ok=True)
    if target == "macos":
        app = sorted(out.glob("*.app"))[0]
        # ditto keeps the bundle's symlinks, permissions and signature intact
        subprocess.run(["ditto", "-c", "-k", "--keepParent", str(app), str(archive)], check=True)
    else:
        # The program and its files at the top of the zip: unzipped into the folder of an older
        # IwaraTool.exe it replaces that program and finds the ``data`` folder beside it.
        shutil.make_archive(str(archive.with_suffix("")), "zip", root_dir=out)
    print(f"Artifact: {archive.relative_to(ROOT)} ({archive.stat().st_size / 1e6:.1f} MB)")
    return archive


def forget_build_bundles(out: Path) -> None:
    """Launch Services registers every .app a build writes; only the installed copy should be listed."""
    bundles = [*out.glob("*.app"), *(STAGE / "build").rglob("Release/*.app")]
    for bundle in bundles:
        subprocess.run([LSREGISTER, "-u", str(bundle)], check=False, capture_output=True)


def install(out: Path) -> None:
    """Replace /Applications/IwaraTool Dev.app with this build (quits the running copy first)."""
    app = sorted(out.glob("*.app"))[0]
    for copy in (TEST_INSTALL, *OLD_TEST_INSTALLS):
        subprocess.run(["pkill", "-f", f"{copy}/Contents/MacOS/"], check=False)
        if copy.exists():
            subprocess.run([LSREGISTER, "-u", str(copy)], check=False, capture_output=True)
            shutil.rmtree(copy)
    subprocess.run(["ditto", str(app), str(TEST_INSTALL)], check=True)
    subprocess.run([LSREGISTER, "-f", str(TEST_INSTALL)], check=False, capture_output=True)
    print(f"Installed: {TEST_INSTALL}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--platform", choices=sorted(PLATFORM_LABELS), default=host_platform())
    parser.add_argument("--label", help="version label in the artifact name (default: v<app version>)")
    parser.add_argument("--no-smoke", action="store_true", help="do not launch the built app")
    parser.add_argument("--install", action="store_true", help=f"macOS: replace the test copy at {TEST_INSTALL}")
    parser.add_argument("--require-signing", action="store_true",
                        help="apk: fail unless the APK is signed with the release key (RELEASE_CERT_SHA256)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    if args.platform in DESKTOP_TARGETS and args.platform != host_platform():
        raise SystemExit(f"{args.platform} can only be built on {args.platform}; this host is {host_platform()}")

    if args.platform == "apk":
        check_signing(args.require_signing)
    version = app_version()
    stage(version)
    outs = [flet_build(args.platform, version, args.verbose)]
    if args.platform == "apk":
        outs.append(flet_build("apk", version, args.verbose, ("--split-per-abi",), name="apk-split"))
    for out in outs:
        audit(out)
        if args.platform == "apk":
            verify_apk_signature(out, args.require_signing)
    out = outs[0]
    if not args.no_smoke and args.platform in DESKTOP_TARGETS:
        smoke(find_executable(args.platform, out), out)
    label = args.label or f"v{version}"
    for built in outs:
        package(args.platform, built, label)
    if args.platform == "apk":
        missing = [abi for abi in (*ANDROID_ABIS, "universal")
                   if not (DIST / f"IwaraTool-Android-{abi}-{label}.apk").is_file()]
        if missing:
            raise SystemExit(f"missing APKs for {', '.join(missing)}")
    if args.platform == "macos":
        forget_build_bundles(out)
        if args.install:
            install(out)


if __name__ == "__main__":
    main()
