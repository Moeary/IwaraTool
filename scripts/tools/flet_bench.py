"""Flet side of the front-end comparison: ``main_flet.py --demo --bench startup|library``.

Started by compare_frontends.py.  ``page.take_screenshot`` returns once the
client has drawn the page, so it marks a real first frame.
"""
from __future__ import annotations

import asyncio
import os
import time

import bench_common


async def _drawn(page) -> None:
    page.enable_screenshots = True
    await page.take_screenshot(pixel_ratio=0.25)


async def downloads(shell, page, report) -> None:
    """Three concurrent downloads: event-loop latency (a 20 ms sleep) and every completion delivered."""
    from app.ui_flet import routes

    await asyncio.sleep(3.0)
    completed: set[str] = set()
    shell.events.on("task_status_changed",
                    lambda task_id="", status="", *_a: completed.add(task_id) if str(status) == "completed" else None)
    shell.select(routes.DOWNLOADS)
    lags: list[float] = []
    started = time.monotonic()
    await asyncio.to_thread(bench_common.start_downloads)
    while time.monotonic() - started < bench_common.DOWNLOAD_TIMEOUT and not bench_common.downloads_finished():
        before = time.perf_counter()
        await asyncio.sleep(bench_common.TICK)
        lags.append(max(0.0, time.perf_counter() - before - bench_common.TICK))
    await asyncio.sleep(1.0)  # let the last events arrive
    report.put(downloads_s=round(time.monotonic() - started, 1), downloads_done=bench_common.downloads_finished(),
               completed_events=len(completed), latency=bench_common.latency_summary(lags))
    report.mark("downloads")


async def run(shell, page, mode: str) -> None:
    from app.ui_flet import routes
    from app.ui_flet.lifecycle import shutdown

    report = bench_common.Report("flet")
    await _drawn(page)
    report.mark("first_frame")
    report.put(mode=mode)
    if mode == "startup":
        await asyncio.sleep(bench_common.SETTLE)
        report.mark("settled")
    elif mode == "downloads":
        await downloads(shell, page, report)
    else:
        records = int(os.environ.get("IWARATOOL_BENCH_RECORDS") or 0)
        report.put(records=records, seed_s=await asyncio.to_thread(bench_common.seed_history, records))
        await asyncio.sleep(3.0)
        library = shell.library
        started = time.monotonic()
        shell.select(routes.LIBRARY)
        if library.state.loaded:
            await library.load()
        while not library.state.loaded or library.state.loading:
            await asyncio.sleep(0.01)
        await _drawn(page)
        report.put(library_open_s=round(time.monotonic() - started, 3), library_rows=len(library.records))
        # What a screenshot round trip costs on its own (every Flet step below includes one).
        bare = []
        for _ in range(5):
            started = time.monotonic()
            await _drawn(page)
            bare.append(time.monotonic() - started)
        report.put(screenshot_ms=round(sorted(bare)[2] * 1000, 1))
        # Scrolling itself runs in the Flutter client; Python only adds the next 200 rows near the end.
        steps = []
        for _ in range(bench_common.SCROLL_STEPS):
            started = time.monotonic()
            library.more()
            await _drawn(page)
            steps.append(time.monotonic() - started)
        report.put(scroll_step_ms=round(sorted(steps)[len(steps) // 2] * 1000, 1),
                   scroll_worst_ms=round(max(steps) * 1000, 1), scroll_rows=library.state.limit,
                   scroll_steps_ms=[round(v * 1000) for v in steps])
        await asyncio.sleep(bench_common.LIBRARY_SETTLE)
        report.mark("library")
        # The same in the cover view (VideoGrid, shared by home, search, subscriptions and authors).
        from app.ui_flet.stores.library import PAGE_ROWS, VIEW_COVER

        library.set_view(VIEW_COVER)
        library.state.limit = PAGE_ROWS  # start the grid at its first page, as on opening
        started = time.monotonic()
        await _drawn(page)
        report.put(grid_open_s=round(time.monotonic() - started, 3))
        grid = []
        for _ in range(bench_common.SCROLL_STEPS):
            started = time.monotonic()
            library.more()
            await _drawn(page)
            grid.append(time.monotonic() - started)
        report.put(grid_step_ms=round(sorted(grid)[len(grid) // 2] * 1000, 1),
                   grid_worst_ms=round(max(grid) * 1000, 1), grid_steps_ms=[round(v * 1000) for v in grid])
        await asyncio.sleep(bench_common.LIBRARY_SETTLE)
        report.mark("grid")
    await shutdown(page)
    os._exit(0)
