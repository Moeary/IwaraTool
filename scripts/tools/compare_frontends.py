"""Compare the PySide6 and Flet front ends on this machine (docs/flet_ui_plan.md P9, flet_migration.md item 2).

    pixi run -e flet python scripts/tools/compare_frontends.py --out tmp/compare [--runs 3] [--records 10000]

Both run offline against demo_fixture (made-up Iwara, throw-away data folder)
at 1280×800, three times each; the medians are reported:

- startup: launch → first drawn frame (including interpreter start and
  imports), and the memory of the process tree 8 s later (home feeds and
  covers loaded; the Flet client is a separate process and is counted);
- library: with ``--records`` made-up history records, the time from
  switching to the library to the drawn page, the memory after, and 20 scroll
  steps (PySide6: one page of the table; Flet: the next 200 rows, the only part
  of scrolling that involves Python — the Flutter client scrolls on its own),
  then the same in the cover view (PySide6 builds every card item on switching;
  Flet builds 200 cards per step);
- downloads: three demo videos at concurrency 3 — total time, how late the UI
  thread runs a 20 ms tick meanwhile (Qt's GUI thread / Flet's event loop),
  and whether every completion event reached the UI.

Each environment's Python is called directly so ``pixi run`` start-up is not
counted.  Needs a desktop session (both open a window).
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench_common  # noqa: E402

BIN = "Scripts" if os.name == "nt" else "bin"
PYTHON = {
    "qt": ROOT / ".pixi" / "envs" / "default" / ("python.exe" if os.name == "nt" else f"{BIN}/python"),
    "flet": ROOT / ".pixi" / "envs" / "flet" / ("python.exe" if os.name == "nt" else f"{BIN}/python"),
}
TIMEOUT = 300


def command(frontend: str, mode: str) -> list[str]:
    if frontend == "qt":
        return [str(PYTHON["qt"]), str(ROOT / "scripts" / "tools" / "bench_qt.py"), mode]
    return [str(PYTHON["flet"]), str(ROOT / "main_flet.py"), "--demo", "--lang", "zh", "--theme", "light",
            "--viewport", "1280x800", "--bench", mode]


def run_once(frontend: str, mode: str, records: int) -> dict:
    with tempfile.TemporaryDirectory(prefix="iwaratool-compare-") as tmp:
        report = Path(tmp) / "report.json"
        env = dict(os.environ, IWARATOOL_BENCH_REPORT=str(report), IWARATOOL_BENCH_RECORDS=str(records),
                   IWARATOOL_DATA_DIR=str(Path(tmp) / "data"), IWARATOOL_DOWNLOAD_DIR=str(Path(tmp) / "dl"))
        if frontend == "qt":
            env["QT_QPA_PLATFORM"] = env.get("QT_QPA_PLATFORM", "cocoa" if sys.platform == "darwin" else "")
            env.pop("IWARATOOL_NO_QT", None)
        else:
            env["IWARATOOL_NO_QT"] = "1"
        env["IWARATOOL_BENCH_IGNORE"] = ",".join(str(p) for p in bench_common.client_pids())  # other Flet windows
        env["IWARATOOL_BENCH_T0"] = repr(time.time())
        result = subprocess.run(command(frontend, mode), cwd=ROOT, env=env, capture_output=True, text=True,
                                timeout=TIMEOUT)
        if not report.exists():
            raise SystemExit(f"{frontend} {mode}: no report (exit {result.returncode})\n{result.stderr[-3000:]}")
        data = json.loads(report.read_text(encoding="utf-8"))
        data["tracebacks"] = (result.stdout + result.stderr).count("Traceback (most recent call last)")
        return data


def median(values: list[float]) -> float:
    return round(statistics.median(values), 2) if values else 0.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=ROOT / "tmp" / "compare")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--records", type=int, default=10_000)
    parser.add_argument("--only", choices=("qt", "flet"), default="")
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    raw: dict[str, list[dict]] = {}
    for frontend in ([args.only] if args.only else ["qt", "flet"]):
        for mode in ("startup", "library", "downloads"):
            key = f"{frontend}-{mode}"
            raw[key] = []
            for attempt in range(args.runs):
                data = run_once(frontend, mode, args.records)
                raw[key].append(data)
                print(key, attempt + 1, json.dumps({k: v for k, v in data.items() if k not in ("pid",)}, ensure_ascii=False))
    (out / "raw.json").write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

    def pick(key: str, *path: str) -> float:
        values = []
        for data in raw.get(key, []):
            value = data
            for part in path:
                value = value.get(part, {}) if isinstance(value, dict) else {}
            if isinstance(value, (int, float)):
                values.append(float(value))
        return median(values)

    rows = [
        ("Launch → first frame (s)", "startup", ("first_frame", "t")),
        ("Memory at first frame (MB)", "startup", ("first_frame", "rss_mb")),
        ("Memory 8 s later, home loaded (MB)", "startup", ("settled", "rss_mb")),
        (f"Open library with {args.records} records (s)", "library", ("library_open_s",)),
        ("Memory with the library open (MB)", "library", ("library", "rss_mb")),
        ("Library scroll step to drawn, median (ms)", "library", ("scroll_step_ms",)),
        ("Library scroll step to drawn, worst (ms)", "library", ("scroll_worst_ms",)),
        ("3 concurrent downloads: total (s)", "downloads", ("downloads_s",)),
        ("…UI thread late on a 20 ms tick, p95 (ms)", "downloads", ("latency", "p95_ms")),
        ("…UI thread late on a 20 ms tick, worst (ms)", "downloads", ("latency", "max_ms")),
        ("…completion events received (of 3)", "downloads", ("completed_events",)),
        ("…memory while downloading (MB)", "downloads", ("downloads", "rss_mb")),
        ("Library cover view: switch to drawn (s)", "library", ("grid_open_s",)),
        ("Cover view scroll step to drawn, median (ms)", "library", ("grid_step_ms",)),
        ("Cover view scroll step to drawn, worst (ms)", "library", ("grid_worst_ms",)),
        ("Memory after the cover view (MB)", "library", ("grid", "rss_mb")),
        ("(Flet: a screenshot round trip alone, included in its steps) (ms)", "library", ("screenshot_ms",)),
    ]
    lines = ["| Measure | PySide6 | Flet |", "| --- | ---: | ---: |"]
    for label, mode, path in rows:
        lines.append(f"| {label} | {pick(f'qt-{mode}', *path)} | {pick(f'flet-{mode}', *path)} |")
    errors = {key: sum(d.get("tracebacks", 0) for d in runs) for key, runs in raw.items()}
    lines.append(f"| Python tracebacks in the runs | {sum(v for k, v in errors.items() if k.startswith('qt'))} | "
                 f"{sum(v for k, v in errors.items() if k.startswith('flet'))} |")
    table = "\n".join(lines)
    (out / "summary.md").write_text(table + "\n", encoding="utf-8")
    print(table)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
