"""PySide6 side of the front-end comparison; started by compare_frontends.py (default pixi environment).

The same start-up steps as main.py / record_demo.py, against demo_fixture's
offline made-up Iwara, in a throw-away data folder.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import bench_common  # noqa: E402
import demo_fixture  # noqa: E402  (must patch before the app is imported)


def pump(app, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def downloads(app, window, report) -> None:
    """Three concurrent downloads: GUI-thread latency (a 20 ms QTimer) and every completion delivered."""
    from PySide6.QtCore import QTimer

    from app.signal_bus import signal_bus

    pump(app, 3.0)
    completed: set[str] = set()
    signal_bus.task_status_changed.connect(
        lambda task_id, status: completed.add(task_id) if str(status) == "completed" else None)
    lags: list[float] = []
    last = [time.perf_counter()]

    def tick() -> None:
        now = time.perf_counter()
        lags.append(max(0.0, now - last[0] - bench_common.TICK))
        last[0] = now

    timer = QTimer()
    timer.setInterval(int(bench_common.TICK * 1000))
    timer.timeout.connect(tick)
    window.switchTo(window._download_page)
    started = time.monotonic()
    bench_common.start_downloads()
    timer.start()
    while time.monotonic() - started < bench_common.DOWNLOAD_TIMEOUT and not bench_common.downloads_finished():
        app.processEvents()
        time.sleep(0.005)
    pump(app, 1.0)  # let the last signals arrive
    timer.stop()
    report.put(downloads_s=round(time.monotonic() - started, 1), downloads_done=bench_common.downloads_finished(),
               completed_events=len(completed), latency=bench_common.latency_summary(lags))
    report.mark("downloads")


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "startup"
    report = bench_common.Report("qt")
    root = demo_fixture.install("zh", "light")
    records = int(os.environ.get("IWARATOOL_BENCH_RECORDS") or 0) if mode == "library" else 0
    report.put(mode=mode, records=records, seed_s=bench_common.seed_history(records))

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    from app.config import app_config
    from app.core.download.manager import download_manager
    from app.core.rules import ensure_temporary_rule
    from app.ui.common import mask_dialogs
    from app.ui.main_window import MainWindow
    from app.ui.style.fonts import apply_app_font
    from app.ui.style.theme import apply_theme_mode

    mask_dialogs.install()
    apply_app_font(app)
    apply_theme_mode(app_config.theme_mode)
    download_manager.restore_cached_login()
    ensure_temporary_rule()
    window = MainWindow()
    download_manager.start()
    window.resize(1280, 800)
    window.show()
    app.processEvents()
    window.grab()  # forces a paint of the whole window
    report.mark("first_frame")
    if mode == "startup":
        pump(app, bench_common.SETTLE)
        report.mark("settled")
    elif mode == "downloads":
        downloads(app, window, report)
    else:
        pump(app, 3.0)
        started = time.monotonic()
        window.switchTo(window._history_page)
        app.processEvents()
        window.grab()
        report.put(library_open_s=round(time.monotonic() - started, 3),
                   library_rows=len(window._history_page._all_records))
        table = window._history_page._table
        bar = table.verticalScrollBar()
        steps = []
        for _ in range(bench_common.SCROLL_STEPS):
            started = time.monotonic()
            bar.setValue(bar.value() + bar.pageStep())
            app.processEvents()
            table.viewport().grab()
            steps.append(time.monotonic() - started)
        report.put(scroll_step_ms=round(sorted(steps)[len(steps) // 2] * 1000, 1),
                   scroll_worst_ms=round(max(steps) * 1000, 1), scroll_rows=bar.value())
        pump(app, bench_common.LIBRARY_SETTLE)
        report.mark("library")
        # The cover view: switching builds every card item; then 20 page scrolls (covers load as they show).
        from app.ui.history.cover_grid import MODE_COVER

        page = window._history_page
        started = time.monotonic()
        page._set_view_mode(MODE_COVER)
        app.processEvents()
        page._cover_grid.viewport().grab()
        report.put(grid_open_s=round(time.monotonic() - started, 3))
        grid_bar = page._cover_grid.verticalScrollBar()
        grid = []
        for _ in range(bench_common.SCROLL_STEPS):
            started = time.monotonic()
            grid_bar.setValue(grid_bar.value() + grid_bar.pageStep())
            app.processEvents()
            page._cover_grid.viewport().grab()
            grid.append(time.monotonic() - started)
        report.put(grid_step_ms=round(sorted(grid)[len(grid) // 2] * 1000, 1), grid_worst_ms=round(max(grid) * 1000, 1))
        pump(app, bench_common.LIBRARY_SETTLE)
        report.mark("grid")
    import shutil

    shutil.rmtree(root, ignore_errors=True)
    os._exit(0)


if __name__ == "__main__":
    main()
