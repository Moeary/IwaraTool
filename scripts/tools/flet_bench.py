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
        await asyncio.sleep(bench_common.LIBRARY_SETTLE)
        report.mark("library")
    await shutdown(page)
    os._exit(0)
