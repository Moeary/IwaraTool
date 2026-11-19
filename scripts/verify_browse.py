"""Open an isolated offline window for manual browsing acceptance checks.

    pixi run python scripts/verify_browse.py --lang zh

Search returns 64 invented posts cycling through portrait, landscape, square,
and unknown resolution. demo000 has a real local smoke video under old_account;
demo001 has a missing-file history record. All HTTP uses demo_fixture.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import demo_fixture


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lang", choices=("en", "zh", "ja"), default="zh")
    args = parser.parse_args()
    original_video = demo_fixture.video

    def video(index, site="iwara"):
        post = original_video(index, site)
        label, width, height = (
            ("Portrait", 1080, 1920), ("Landscape", 1920, 1080),
            ("Square", 1080, 1080), ("Unknown", 0, 0),
        )[index % 4]
        post["title"] = f"{label} {post['title']}"
        post["file"].update(width=width, height=height)
        return post

    demo_fixture.video = video
    root = demo_fixture.install(args.lang)
    from app.config import app_config
    app_config.search_auto_search_enabled = False

    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication
    from app.core.download.manager import download_manager
    from app.core.rules import ensure_temporary_rule
    from app.ui.common import mask_dialogs
    from app.ui.main_window import MainWindow
    from app.ui.style.fonts import apply_app_font
    from app.ui.style.theme import apply_theme_mode
    from record_demo import seed

    app = QApplication(sys.argv)
    app.setApplicationName("IwaraTool Browse Verification")
    mask_dialogs.install()
    apply_app_font(app)
    apply_theme_mode(app_config.theme_mode)
    ensure_temporary_rule()
    seed(root)
    folder = root / "Downloads" / "old_account"
    folder.mkdir()
    local = folder / "Portrait Starlight Dance_demo000.mp4"
    shutil.copyfile(ROOT / "tests" / "fixtures" / "video-smoke.mp4", local)
    for index, path in ((0, local), (1, folder / "missing_demo001.mp4")):
        post = video(index)
        download_manager.history.upsert_downloaded({
            "video_id": post["id"], "title": post["title"], "file_path": str(path),
            "author": post["user"]["username"], "source_url": f"https://www.iwara.tv/video/{post['id']}",
        })
    window = MainWindow()
    window.setWindowIcon(QIcon(str(ROOT / "app" / "icon.ico")))
    window.resize(1280, 800)
    window.show()
    window.raise_()
    window.activateWindow()
    print(f"Verification data: {root}", flush=True)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
