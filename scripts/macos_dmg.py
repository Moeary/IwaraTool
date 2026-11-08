"""Package IwaraTool.app as a drag-to-Applications DMG and verify it.

The installer window background is drawn with Qt at build time (1x and @2x),
so there is no binary artwork to keep in sync with the text.  ``dmgbuild``
writes the Finder layout directly, without scripting Finder, which is what
keeps this working on headless CI runners.
"""
from __future__ import annotations

import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path

WINDOW_SIZE = (660, 420)
ICON_SIZE = 112
APP_ICON_POS = (170, 190)
APPLICATIONS_POS = (490, 190)

# Finder draws the icon labels in black (light mode) or white (dark mode) and a
# custom background cannot change that, so the background is a mid-tone slate
# that keeps both readable.
_BACKGROUND_TOP = "#8a95a5"
_BACKGROUND_BOTTOM = "#6b7686"

INSTALL_LINES = (
    "将 IwaraTool 拖到 Applications 文件夹即可完成安装",
    "Drag IwaraTool onto Applications to install",
)
FIRST_LAUNCH_LINES = (
    "首次打开被拦截？系统设置 › 隐私与安全性 › 仍要打开",
    "Blocked on first launch? System Settings › Privacy & Security › Open Anyway",
)


def render_background(path: Path, scale: int = 1) -> Path:
    """Draw the installer background at ``scale`` (1 or 2) into ``path``."""

    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import (
        QColor, QFont, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QPen,
    )

    _app = QGuiApplication.instance() or QGuiApplication([])
    width, height = WINDOW_SIZE
    image = QImage(width * scale, height * scale, QImage.Format.Format_ARGB32)
    image.setDevicePixelRatio(scale)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

    gradient = QLinearGradient(0, 0, 0, height)
    gradient.setColorAt(0, QColor(_BACKGROUND_TOP))
    gradient.setColorAt(1, QColor(_BACKGROUND_BOTTOM))
    painter.fillRect(QRectF(0, 0, width, height), gradient)

    # Arrow from the app icon towards the Applications folder.
    y = APP_ICON_POS[1]
    start_x = APP_ICON_POS[0] + ICON_SIZE / 2 + 22
    end_x = APPLICATIONS_POS[0] - ICON_SIZE / 2 - 22
    pen = QPen(QColor(255, 255, 255, 230), 6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.drawLine(QPointF(start_x, y), QPointF(end_x - 14, y))
    head = QPainterPath(QPointF(end_x, y))
    head.lineTo(end_x - 24, y - 16)
    head.lineTo(end_x - 24, y + 16)
    head.closeSubpath()
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(255, 255, 255, 230))
    painter.drawPath(head)

    def draw_lines(lines: tuple[str, ...], top: float, size: int, weight: QFont.Weight, alpha: int):
        font = QFont()
        font.setPixelSize(size)
        font.setWeight(weight)
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255, alpha))
        line_height = size + 8
        for index, text in enumerate(lines):
            rect = QRectF(16, top + index * line_height, width - 32, line_height)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

    draw_lines(INSTALL_LINES, 290, 17, QFont.Weight.DemiBold, 255)
    draw_lines(FIRST_LAUNCH_LINES, 346, 12, QFont.Weight.Normal, 225)
    painter.end()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not image.save(str(path), "PNG"):
        raise RuntimeError(f"Could not write {path}")
    return path


def dmg_settings(app: Path, background: Path, icon: Path | None) -> dict:
    width, height = WINDOW_SIZE
    settings = {
        "format": "UDZO",
        "filesystem": "HFS+",
        "files": [str(app)],
        "symlinks": {"Applications": "/Applications"},
        "icon_locations": {app.name: APP_ICON_POS, "Applications": APPLICATIONS_POS},
        "background": str(background),
        "window_rect": ((200, 120), (width, height)),
        "icon_size": float(ICON_SIZE),
        "text_size": 13.0,
        "default_view": "icon-view",
        "show_status_bar": False,
        "show_toolbar": False,
        "show_pathbar": False,
        "show_sidebar": False,
        # Do not use hide_extensions for the signed .app: dmgbuild's SetFile
        # call adds FinderInfo, which makes strict code-signature checks fail.
    }
    if icon is not None:
        settings["icon"] = str(icon)
    return settings


def build_dmg(app: Path, output: Path, *, work_dir: Path, icon: Path | None = None) -> Path:
    import dmgbuild

    background_dir = work_dir / "dmg-background"
    background = render_background(background_dir / "background.png", 1)
    render_background(background_dir / "background@2x.png", 2)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    dmgbuild.build_dmg(str(output), "IwaraTool", settings=dmg_settings(app, background, icon))
    print(f"Created {output}")
    return output


@contextmanager
def mounted(dmg: Path):
    with tempfile.TemporaryDirectory(prefix="iwaratool-dmg-") as mount_point:
        subprocess.run(
            ["/usr/bin/hdiutil", "attach", "-nobrowse", "-readonly", "-noautoopen", "-mountpoint", mount_point, str(dmg)],
            check=True, capture_output=True,
        )
        try:
            yield Path(mount_point)
        finally:
            subprocess.run(["/usr/bin/hdiutil", "detach", mount_point, "-force"], check=False, capture_output=True)


def verify_dmg_layout(volume: Path, app_name: str) -> list[str]:
    """What a user dragging the app out of the DMG needs to find there."""

    issues: list[str] = []
    if not (volume / app_name / "Contents" / "MacOS").is_dir():
        issues.append(f"{app_name} is missing from the DMG")
    applications = volume / "Applications"
    if not applications.is_symlink() or str(applications.readlink()) != "/Applications":
        issues.append("the Applications shortcut is missing from the DMG")
    if not any(volume.glob(".background.*")):
        issues.append("the installer background is missing from the DMG")
    return issues
