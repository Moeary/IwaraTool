"""Shared pieces of the front-end comparison (scripts/tools/compare_frontends.py).

No Qt and no Flet here: both runners import it.  The report is a JSON file
named by ``IWARATOOL_BENCH_REPORT``; ``IWARATOOL_BENCH_T0`` is the
orchestrator's ``time.time()`` just before it started the process, so
"seconds since launch" includes interpreter start and imports.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

SETTLE = 8.0  # seconds after the first frame for covers and feeds (both front ends)
LIBRARY_SETTLE = 3.0


def since_launch() -> float:
    return round(time.time() - float(os.environ.get("IWARATOOL_BENCH_T0") or time.time()), 3)


# The Flet desktop client: on macOS ``open -n`` starts it, so launchd (not us) is its parent.
CLIENT_MARKERS = ("Flet.app/Contents/MacOS/", "flet.exe", "/flet/flet ")


def client_pids() -> set[int]:
    """Flet client processes running now (the orchestrator leaves out the ones that predate a run)."""
    try:
        out = subprocess.run(["ps", "-A", "-o", "pid=,args="], capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return set()
    found = set()
    for line in out.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and any(marker in parts[1] + " " for marker in CLIENT_MARKERS):
            found.add(int(parts[0]))
    return found


def _ignored() -> set[int]:
    return {int(v) for v in os.environ.get("IWARATOOL_BENCH_IGNORE", "").split(",") if v.strip().isdigit()}


def tree_rss_mb(pid: int | None = None, *, client: bool = False) -> float:
    """Resident memory of a process and its descendants, plus this run's Flet client when ``client``."""
    pid = pid or os.getpid()
    ignored = _ignored()
    try:
        out = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,rss=,args="], capture_output=True, text=True,
                             check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return 0.0
    children: dict[int, list[int]] = {}
    rss: dict[int, int] = {}
    clients: list[int] = []
    for line in out.splitlines():
        parts = line.split(None, 3)
        if len(parts) < 3:
            continue
        p, pp, r = (int(v) for v in parts[:3])
        children.setdefault(pp, []).append(p)
        rss[p] = r
        if client and p not in ignored and len(parts) == 4 and any(marker in parts[3] + " " for marker in CLIENT_MARKERS):
            clients.append(p)
    seen: set[int] = set()
    total, stack = 0, [pid, *clients]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        total += rss.get(current, 0)
        stack.extend(children.get(current, []))
    return round(total / 1024, 1)


def seed_history(count: int) -> float:
    """``count`` made-up downloaded records (no files); returns the seconds it took (not part of any timing)."""
    if count <= 0:
        return 0.0
    import demo_fixture

    from app.core.download.manager import download_manager

    started = time.monotonic()
    for index in range(count):
        post = demo_fixture.video(1000 + index)
        download_manager.history.upsert_downloaded({
            "video_id": f"bench{index:06d}", "title": f"{post['title']} {index}", "author": post["user"]["username"],
            "published_at": post["createdAt"], "likes": post["numLikes"], "views": post["numViews"],
            "duration": post["file"]["duration"], "rating": "general",
            "source_url": f"https://www.iwara.tv/video/bench{index:06d}", "file_path": f"/nowhere/bench{index:06d}.mp4",
            "quality": "Source",
        })
    return round(time.monotonic() - started, 1)


class Report:
    def __init__(self, frontend: str):
        self.path = Path(os.environ["IWARATOOL_BENCH_REPORT"])
        self.data: dict = {"frontend": frontend, "pid": os.getpid()}

    def mark(self, name: str, **extra) -> None:
        self.data[name] = {"t": since_launch(), "rss_mb": tree_rss_mb(client=self.data["frontend"] == "flet"), **extra}
        self.write()

    def put(self, **values) -> None:
        self.data.update(values)
        self.write()

    def write(self) -> None:
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
