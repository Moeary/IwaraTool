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
    else:
        pump(app, 3.0)
        started = time.monotonic()
        window.switchTo(window._history_page)
        app.processEvents()
        window.grab()
        report.put(library_open_s=round(time.monotonic() - started, 3),
                   library_rows=len(window._history_page._all_records))
        pump(app, bench_common.LIBRARY_SETTLE)
        report.mark("library")
    import shutil

    shutil.rmtree(root, ignore_errors=True)
    os._exit(0)


if __name__ == "__main__":
    main()
