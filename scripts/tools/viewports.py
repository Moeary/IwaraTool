"""Screenshot the Flet interface at the sizes of docs/flet_ui_plan.md 9.2.

    pixi run python scripts/tools/viewports.py --out tmp/viewports
    pixi run python scripts/tools/viewports.py --route /settings --theme dark --only 360x800,1440x900

Every run uses a throw-away data directory, so the developer's settings stay
untouched.  Each size is a separate start of ``main.py``.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SIZES = (
    "320x640", "344x882", "360x800", "411x960", "960x411", "673x841",
    "841x673", "800x1280", "1280x800", "1440x900", "2560x1440",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=ROOT / "tmp" / "viewports")
    parser.add_argument("--route", default="")
    parser.add_argument("--theme", choices=("auto", "light", "dark"), default="light")
    parser.add_argument("--lang", choices=("zh", "en", "ja"), default="zh")
    parser.add_argument("--account", action="store_true", help="open the account panel")
    parser.add_argument("--drawer", action="store_true", help="open the phone navigation drawer")
    parser.add_argument("--demo", action="store_true", help="offline made-up data (demo_fixture)")
    parser.add_argument("--wait", type=float, default=1.5, help="seconds before each screenshot")
    parser.add_argument("--only", default="", help="comma-separated sizes (default: all of 9.2)")
    parser.add_argument("--prefix", default="", help="file name prefix")
    parser.add_argument("--platform", choices=("android", "ios"), default="", help="behave as a phone platform (8.3)")
    args = parser.parse_args()
    sizes = [s.strip() for s in args.only.split(",") if s.strip()] or list(SIZES)
    args.out.mkdir(parents=True, exist_ok=True)
    failures = 0
    with tempfile.TemporaryDirectory(prefix="iwaratool-viewports-") as data:
        env = dict(os.environ, IWARATOOL_DATA_DIR=data, IWARATOOL_DOWNLOAD_DIR=str(Path(data) / "downloads"))
        for size in sizes:
            shot = (args.out / f"{args.prefix}{size}.png").resolve()
            command = [sys.executable, str(ROOT / "main.py"), "--viewport", size, "--shot", str(shot),
                       "--theme", args.theme, "--lang", args.lang]
            if args.route:
                command += ["--route", args.route]
            if args.account:
                command.append("--account")
            if args.drawer:
                command.append("--drawer")
            if args.demo:
                command.append("--demo")
            if args.platform:
                command += ["--platform", args.platform]
            command += ["--wait", str(args.wait)]
            result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
            ok = result.returncode == 0 and shot.exists()
            failures += not ok
            print(f"{'ok  ' if ok else 'FAIL'} {size} -> {shot}")
            if not ok:
                print(result.stdout[-2000:], result.stderr[-4000:], sep="\n")
            for line in (result.stdout + result.stderr).splitlines():
                if "Traceback" in line or "Exception" in line or "overflowed" in line:
                    print("   ", line)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
