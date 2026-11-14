"""Record the README demo GIF and page screenshots, offline and with made-up content.

    pixi run python scripts/record_demo.py              # GIF + screenshots into docs/
    pixi run python scripts/record_demo.py --no-gif     # screenshots only
    pixi run python scripts/record_demo.py --out /tmp/demo --lang zh

The app runs against ``demo_fixture``: an isolated data folder and a fake
Iwara whose posts, authors and comments are invented and whose every image is
generated abstract artwork, so no real cover, avatar or account appears.  A
scripted tour drives the real window while frames are grabbed from it; a
drawn pointer shows where each click lands.  ffmpeg turns the frames into the
GIF.  Needs a display (run it on a desktop, not headless).
"""
from __future__ import annotations

import argparse
import base64
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import demo_fixture  # noqa: E402  (must patch before the app is imported)

WINDOW = (1280, 800)
FPS = 10


def _fake_token() -> str:
    def part(value: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

    return f"{part({'alg': 'none'})}.{part({'exp': 4102444800, 'sub': 'demo'})}.demo"


class Tour:
    """Drives the window and grabs frames while it waits."""

    def __init__(self, app, window, frames_dir: Path | None):
        from PySide6.QtCore import QPointF

        self.app = app
        self.window = window
        self.frames_dir = frames_dir
        self.frame = 0
        self.pointer = QPointF(WINDOW[0] * 0.55, WINDOW[1] * 0.6)
        self.ripple: tuple[QPointF, float] | None = None
        self._next_grab = time.monotonic()

    # ── time and frames ─────────────────────────────────────────────────────

    def wait(self, seconds: float):
        end = time.monotonic() + seconds
        while True:
            self.app.processEvents()
            now = time.monotonic()
            if self.frames_dir is not None and now >= self._next_grab:
                self._grab()
                self._next_grab += 1 / FPS
                if self._next_grab < now:  # fell behind: do not burst
                    self._next_grab = now + 1 / FPS
            if now >= end:
                return
            time.sleep(0.005)

    def _grab(self):
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen

        pixmap = self.window.grab()
        image = pixmap.toImage().scaled(
            WINDOW[0], WINDOW[1], Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation,
        )
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.ripple is not None:
            center, started = self.ripple
            age = time.monotonic() - started
            if age < 0.45:
                radius = 10 + age * 60
                painter.setPen(QPen(QColor(0, 120, 212, int(200 * (1 - age / 0.45))), 3))
                painter.setBrush(QColor(0, 120, 212, int(60 * (1 - age / 0.45))))
                painter.drawEllipse(center, radius, radius)
            else:
                self.ripple = None
        x, y = self.pointer.x(), self.pointer.y()
        arrow = QPainterPath(QPointF(x, y))
        for dx, dy in ((0, 21), (5, 16), (9, 25), (12.5, 23.5), (8.5, 15), (15, 15)):
            arrow.lineTo(x + dx, y + dy)
        arrow.closeSubpath()
        painter.setPen(QPen(QColor(255, 255, 255), 1.6))
        painter.setBrush(QColor(20, 20, 20))
        painter.drawPath(arrow)
        painter.end()
        image.save(str(self.frames_dir / f"frame_{self.frame:05d}.png"))
        self.frame += 1

    # ── pointer ─────────────────────────────────────────────────────────────

    def point_of(self, widget, dx: float = 0.5, dy: float = 0.5):
        from PySide6.QtCore import QPoint, QPointF

        local = QPoint(int(widget.width() * dx), int(widget.height() * dy))
        target = widget.mapTo(self.window, local)
        return QPointF(target)

    def move(self, target, seconds: float = 0.6):
        from PySide6.QtCore import QPointF

        if not isinstance(target, QPointF):
            target = self.point_of(target)
        start = QPointF(self.pointer)
        began = time.monotonic()
        while True:
            t = min(1.0, (time.monotonic() - began) / seconds)
            eased = 0.5 - math.cos(math.pi * t) / 2
            self.pointer = start + (target - start) * eased
            self.wait(1 / FPS / 2)
            if t >= 1:
                return

    def click(self, widget, action=None, *, dx: float = 0.5, dy: float = 0.5, after: float = 0.5):
        """Glide to ``widget``, show the click, then run ``action`` (default: ``widget.click()``)."""

        from PySide6.QtCore import QPointF

        self.move(self.point_of(widget, dx, dy))
        self.ripple = (QPointF(self.pointer), time.monotonic())
        self.wait(0.12)
        (action or widget.click)()
        self.wait(after)

    def type_into(self, edit, text: str, per_char: float = 0.06):
        edit.setFocus()
        for index in range(len(text) + 1):
            edit.setText(text[:index])
            self.wait(per_char)

    def scroll(self, area, to: int, seconds: float = 0.8):
        bar = area.verticalScrollBar()
        start = bar.value()
        target = max(bar.minimum(), min(bar.maximum(), to))
        began = time.monotonic()
        while True:
            t = min(1.0, (time.monotonic() - began) / seconds)
            bar.setValue(int(start + (target - start) * (0.5 - math.cos(math.pi * t) / 2)))
            self.wait(1 / FPS / 2)
            if t >= 1:
                return

    def until(self, condition, timeout: float = 15.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if condition():
                return True
            self.wait(0.05)
        print("  (timed out waiting)", file=sys.stderr)
        return False

    def shot(self, path: Path):
        """Save the window as it is, minus any passing notification bar."""

        from qfluentwidgets import InfoBar

        path.parent.mkdir(parents=True, exist_ok=True)
        self.wait(0.05)
        bars = [bar for bar in self.window.findChildren(InfoBar) if bar.isVisible()]
        for bar in bars:
            bar.hide()
        self.window.grab().save(str(path), "JPG", 90)
        for bar in bars:
            bar.show()
        print(f"  screenshot {path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}")


# ── data the pages start with ───────────────────────────────────────────────


def seed(root: Path):
    """History records with files on disk, and subscriptions filled by a (fake) refresh."""

    from app.core.manager import download_manager

    downloads = root / "Downloads"
    for index in range(30, 52):
        post = demo_fixture.video(index)
        author = post["user"]["username"]
        folder = downloads / author
        folder.mkdir(parents=True, exist_ok=True)
        file_path = folder / f"{post['createdAt'][:10]}_{post['title']}_{post['id']}.mp4"
        file_path.write_bytes(b"\0" * 1024)
        thumb = folder / f"{file_path.stem}.jpg"
        thumb.write_bytes(demo_fixture.artwork(f"/image/original/file-{post['id']}/thumbnail.jpg"))
        download_manager.history.upsert_downloaded({
            "video_id": post["id"], "title": post["title"], "author": author,
            "published_at": post["createdAt"], "likes": post["numLikes"], "views": post["numViews"],
            "duration": post["file"]["duration"], "comments": post["numComments"], "rating": "general",
            "tags_json": json.dumps([tag["id"] for tag in post["tags"]]), "raw_json": json.dumps(post),
            "source_url": f"https://www.iwara.tv/video/{post['id']}", "file_path": str(file_path),
            "thumbnail_path": str(thumb), "quality": "Source",
        })
    for username, name in demo_fixture.AUTHORS[:6]:
        download_manager.add_author_subscription(username, title=name, remote_id=f"user-{username}")
    download_manager.refresh_all_subscriptions()


# ── the tour ────────────────────────────────────────────────────────────────


def tour(t: Tour, shots: Path | None):
    from PySide6.QtCore import Qt

    w = t.window
    home = w._home_page
    print("Home")
    t.until(lambda: any(block._grid.videos() for block in home._feed.blocks))
    t.wait(1.6)
    if shots:
        t.shot(shots / "iwaratool_home_panel.jpg")
    t.scroll(home._feed._scroll, 420, 1.0)
    t.wait(0.6)
    block = next(b for b in home._feed.blocks if b._grid.videos())
    card = block._grid._cards[1]
    t.click(card, lambda: home._open_video(card.video), after=1.8)
    t.scroll(home._detail._scroll, 520, 1.2)
    t.wait(1.2)
    t.click(w.navigationInterface, w.navigate_back, dx=0.5, dy=0.03, after=0.8)

    print("Search")
    search = w._search_page
    t.click(w.navigationInterface, lambda: w.switchTo(search), dx=0.5, dy=0.23, after=0.6)
    t.click(search._keyword_edit, search._keyword_edit.setFocus, after=0.2)
    t.type_into(search._keyword_edit, "dance")
    t.click(search._search_btn if hasattr(search, "_search_btn") else search._keyword_edit,
            lambda: search._start_search(), after=0.4)
    t.until(lambda: bool(search._all_videos))
    t.wait(1.8)
    if shots:
        t.shot(shots / "iwaratool_search_panel.jpg")

    print("Download Hub")
    download = w._download_page
    t.click(w.navigationInterface, lambda: w.switchTo(download), dx=0.5, dy=0.28, after=0.5)
    t.click(download._url_edit, download._url_edit.setFocus, after=0.2)
    t.type_into(download._url_edit, "https://www.iwara.tv/profile/aoi_motion", per_char=0.025)
    t.click(download._submit_btn, after=0.3)
    panel = download._staging_panel
    t.until(lambda: panel.isVisible() and panel._confirm_btn.isEnabled())
    t.wait(1.4)
    t.click(panel._confirm_btn, panel._confirm, after=0.5)
    t.wait(5.0)
    if shots:
        t.shot(shots / "iwaratool_download_panel.jpg")

    print("Subscriptions")
    subs = w._subscription_page
    t.click(w.navigationInterface, lambda: w.switchTo(subs), dx=0.5, dy=0.18, after=2.0)
    if shots:
        t.shot(shots / "iwaratool_subscription_panel.jpg")
    t.wait(1.0)

    print("Tools")
    tools = w._tools_page
    t.click(w.navigationInterface, lambda: w.switchTo(tools), dx=0.5, dy=0.92, after=1.6)
    if shots:
        t.shot(shots / "iwaratool_tools_panel.jpg")

    print("iwara.ai")
    t.click(w.navigationInterface, lambda: w.switchTo(home), dx=0.5, dy=0.13, after=0.4)
    t.scroll(home._feed._scroll, 0, 0.6)
    t.click(w.navigationInterface, w.toggle_site, dx=0.5, dy=0.77, after=0.3)
    t.until(lambda: any(
        str(getattr(v, "source_url", "")).startswith("https://www.iwara.ai") for b in home._feed.blocks for v in b._grid.videos()
    ))
    t.wait(3.0)
    t.frames_dir = None  # the GIF ends here; the rest only takes screenshots

    if shots:
        print("Other pages")
        w.toggle_site()
        t.wait(0.5)
        for page, name in (
            (w._history_page, "iwaratool_history_panel.jpg"),
            (w._rules_page, "iwaratool_rule_panel.jpg"),
            (w._settings_page, "iwaratool_settings_panel.jpg"),
        ):
            w.switchTo(page)
            t.wait(1.5)
            t.shot(shots / name)


def make_gif(frames: Path, output: Path, width: int):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit("ffmpeg is needed to build the GIF")
    graph = (
        f"fps={FPS},scale={width}:-1:flags=lanczos,split[a][b];"
        "[a]palettegen=max_colors=192:stats_mode=diff[p];"
        "[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle"
    )
    subprocess.run([
        ffmpeg, "-v", "error", "-y", "-framerate", str(FPS), "-i", str(frames / "frame_%05d.png"),
        "-filter_complex", graph, "-loop", "0", str(output),
    ], check=True)
    print(f"GIF {output} ({output.stat().st_size / 1e6:.1f} MB)")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=ROOT / "docs", help="folder for the GIF and panel_view/")
    parser.add_argument("--lang", default="en", choices=("en", "zh", "ja"))
    parser.add_argument("--theme", default="light", choices=("light", "dark"))
    parser.add_argument("--gif-width", type=int, default=1000)
    parser.add_argument("--no-gif", action="store_true")
    parser.add_argument("--no-shots", action="store_true")
    args = parser.parse_args()

    root = demo_fixture.install(args.lang, args.theme)
    from app.config import app_config

    app_config.auth_enabled = True
    app_config.username = "demo_user"
    app_config.password = "demo-password"
    app_config.auth_token = _fake_token()

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("IwaraTool")
    # The same start-up steps as main.py.
    from app.core.manager import download_manager
    from app.core.rules import ensure_temporary_rule
    from app.ui import mask_dialogs
    from app.ui.fonts import apply_app_font
    from app.ui.main_window import MainWindow
    from app.ui.theme import apply_theme_mode

    mask_dialogs.install()
    apply_app_font(app)
    apply_theme_mode(app_config.theme_mode)
    download_manager.restore_cached_login()
    ensure_temporary_rule()
    print(f"Demo data in {root}")
    seed(root)

    window = MainWindow()
    download_manager.start()
    window.setWindowIcon(QIcon(str(ROOT / "app" / "icon.ico")))
    window.resize(*WINDOW)
    window.show()
    window.raise_()
    window.activateWindow()
    frames = None if args.no_gif else Path(tempfile.mkdtemp(prefix="iwaratool-frames-"))
    t = Tour(app, window, frames)
    t.wait(0.5)
    try:
        tour(t, None if args.no_shots else args.out / "panel_view")
    finally:
        if frames is not None and t.frame:
            make_gif(frames, args.out / "iwaratool_demo.gif", args.gif_width)
            shutil.rmtree(frames, ignore_errors=True)
        shutil.rmtree(root, ignore_errors=True)
    os._exit(0)


if __name__ == "__main__":
    main()
