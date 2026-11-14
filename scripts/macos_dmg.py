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

WINDOW_SIZE = (680, 480)
ICON_SIZE = 112
APP_ICON_POS = (190, 196)
APPLICATIONS_POS = (490, 196)

# Finder draws the icon labels in black (light mode) or white (dark mode) and a
# custom background cannot change that, so the background is a mid-tone
# lavender (the logo's colour) that keeps both readable.
_BACKGROUND_TOP = "#958bd0"
_BACKGROUND_BOTTOM = "#6c64a8"
_MINT = "#b8f0cf"
_PINK = "#f7c6e6"

# A disk image cannot follow the viewer's language, so every line is shown in
# Chinese, Japanese and English, each in a font that draws its script natively.
INSTALL_LINES = (
    ("zh", "将 IwaraTool 拖到 Applications 文件夹即可完成安装"),
    ("ja", "IwaraTool を Applications フォルダへドラッグしてインストール"),
    ("en", "Drag IwaraTool onto Applications to install"),
)
FIRST_LAUNCH_LINES = (
    ("zh", "首次打开被拦截？系统设置 › 隐私与安全性 › 仍要打开"),
    ("ja", "初回起動がブロックされたら：システム設定 › プライバシーとセキュリティ › このまま開く"),
    ("en", "Blocked on first launch? System Settings › Privacy & Security › Open Anyway"),
)
_FAMILIES = {
    "zh": ["PingFang SC", "Hiragino Sans GB", "Heiti SC"],
    "ja": ["Hiragino Sans", "Hiragino Kaku Gothic ProN", "PingFang SC"],
    "en": ["SF Pro Text", "Helvetica Neue", "Helvetica"],
}


def render_background(path: Path, scale: int = 1, version: str = "") -> Path:
    """Draw the installer background at ``scale`` (1 or 2) into ``path``."""

    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import (
        QColor, QFont, QFontMetricsF, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QPen,
    )

    _app = QGuiApplication.instance() or QGuiApplication([])
    width, height = WINDOW_SIZE
    image = QImage(width * scale, height * scale, QImage.Format.Format_ARGB32)
    image.setDevicePixelRatio(scale)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

    gradient = QLinearGradient(0, 0, width * 0.35, height)
    gradient.setColorAt(0, QColor(_BACKGROUND_TOP))
    gradient.setColorAt(1, QColor(_BACKGROUND_BOTTOM))
    painter.fillRect(QRectF(0, 0, width, height), gradient)

    def font(language: str, size: float, weight: QFont.Weight) -> QFont:
        result = QFont()
        result.setFamilies(_FAMILIES.get(language, _FAMILIES["en"]))
        result.setPixelSize(round(size))
        result.setWeight(weight)
        return result

    def sparkle(x: float, y: float, r: float, color: str, alpha: int):
        # A four-pointed star: tips on the axes, waist on the diagonals.
        star = QPainterPath(QPointF(x, y - r))
        for dx, dy in ((0.28, -0.28), (1, 0), (0.28, 0.28), (0, 1), (-0.28, 0.28), (-1, 0), (-0.28, -0.28)):
            star.lineTo(x + dx * r, y + dy * r)
        star.closeSubpath()
        tint = QColor(color)
        tint.setAlpha(alpha)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(tint)
        painter.drawPath(star)

    # A few of the logo's sparkles, kept faint so they never compete with the icons.
    for x, y, r, color, alpha in (
        (52, 64, 9, _MINT, 150), (628, 52, 7, _PINK, 170), (612, 300, 6, _MINT, 120),
        (70, 300, 6, _PINK, 140), (340, 92, 5, "#ffffff", 120),
    ):
        sparkle(x, y, r, color, alpha)

    # Title and version.
    title_font = font("en", 24, QFont.Weight.Bold)
    painter.setFont(title_font)
    title = "IwaraTool"
    title_width = QFontMetricsF(title_font).horizontalAdvance(title)
    badge_font = font("en", 12, QFont.Weight.DemiBold)
    badge_text = f"v{version}" if version else ""
    badge_width = QFontMetricsF(badge_font).horizontalAdvance(badge_text) + 18 if badge_text else 0
    gap = 10 if badge_text else 0
    left = (width - title_width - gap - badge_width) / 2
    painter.setPen(QColor(255, 255, 255))
    painter.drawText(QRectF(left, 26, title_width + 2, 34), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, title)
    if badge_text:
        badge = QRectF(left + title_width + gap, 33, badge_width, 21)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 56))
        painter.drawRoundedRect(badge, 10.5, 10.5)
        painter.setFont(badge_font)
        painter.setPen(QColor(255, 255, 255, 240))
        painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, badge_text)

    # Soft plates behind the two icons (their Finder labels sit inside them).
    for cx, cy in (APP_ICON_POS, APPLICATIONS_POS):
        plate = QRectF(cx - 84, cy - 80, 168, 178)
        painter.setPen(QPen(QColor(255, 255, 255, 70), 1.2))
        painter.setBrush(QColor(255, 255, 255, 30))
        painter.drawRoundedRect(plate, 26, 26)

    # Arrow from the app towards the Applications folder, in the logo's mint.
    y = APP_ICON_POS[1]
    start_x = APP_ICON_POS[0] + 84 + 16
    end_x = APPLICATIONS_POS[0] - 84 - 16
    arrow = QColor(_MINT)
    pen = QPen(arrow, 5, Qt.PenStyle.DotLine, Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.drawLine(QPointF(start_x, y), QPointF(end_x - 18, y))
    head = QPainterPath(QPointF(end_x, y))
    head.lineTo(end_x - 20, y - 14)
    head.lineTo(end_x - 20, y + 14)
    head.closeSubpath()
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(arrow)
    painter.drawPath(head)

    def draw_lines(lines, top: float, size: float, weight: QFont.Weight, alpha: int, line_height: float):
        painter.setPen(QColor(255, 255, 255, alpha))
        for index, (language, text) in enumerate(lines):
            painter.setFont(font(language, size, weight))
            rect = QRectF(16, top + index * line_height, width - 32, line_height)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

    draw_lines(INSTALL_LINES, 314, 15, QFont.Weight.DemiBold, 255, 23)

    # First-launch help in a quiet box at the bottom.
    box = QRectF(40, 398, width - 80, 66)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(40, 30, 90, 46))
    painter.drawRoundedRect(box, 14, 14)
    draw_lines(FIRST_LAUNCH_LINES, box.top() + 5, 11.5, QFont.Weight.Normal, 228, 19)
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


def build_dmg(app: Path, output: Path, *, work_dir: Path, icon: Path | None = None, version: str = "") -> Path:
    import dmgbuild

    background_dir = work_dir / "dmg-background"
    background = render_background(background_dir / "background.png", 1, version)
    render_background(background_dir / "background@2x.png", 2, version)
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
