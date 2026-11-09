"""Interface fonts: a platform-adaptive base stack, optionally led by the user's pick.

The stack is an ordered family list.  Qt (both ``QFont.setFamilies`` and the
QSS ``font-family`` list that qfluentwidgets builds from ``qconfig``) looks a
glyph up family by family and finally in the system fallback, so a chosen
font that lacks Chinese or Japanese characters still shows them through the
families behind it.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterable

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

from ..config import app_config
from ..i18n import current_language
from ..logging_setup import get_logger

_log = get_logger(__name__)

AUTO_FONT = ""  # ``ui_font_family`` value meaning "follow the platform"

# Per platform: Latin UI font, then Simplified Chinese and Japanese UI fonts.
# The system UI font Qt reports is put in front of the Latin list at runtime.
_PLATFORM_FONTS: dict[str, dict[str, tuple[str, ...]]] = {
    "windows": {
        "latin": ("Segoe UI",),
        "zh": ("Microsoft YaHei UI", "Microsoft YaHei", "SimHei"),
        "ja": ("Yu Gothic UI", "Meiryo UI", "Meiryo", "MS UI Gothic"),
    },
    "macos": {
        "latin": ("Helvetica Neue",),
        "zh": ("PingFang SC", "Hiragino Sans GB", "Heiti SC"),
        "ja": ("Hiragino Sans", "Hiragino Kaku Gothic ProN", "Osaka"),
    },
    "linux": {
        "latin": ("Noto Sans", "Cantarell", "Ubuntu", "DejaVu Sans"),
        "zh": ("Noto Sans CJK SC", "Source Han Sans SC", "WenQuanYi Micro Hei", "Droid Sans Fallback"),
        "ja": ("Noto Sans CJK JP", "Source Han Sans JP", "IPAPGothic", "TakaoPGothic"),
    },
}

_MONO_FONTS: dict[str, tuple[str, ...]] = {
    "windows": ("Cascadia Mono", "Consolas", "Courier New"),
    "macos": ("SF Mono", "Menlo", "Monaco", "Courier New"),
    "linux": ("DejaVu Sans Mono", "Noto Sans Mono", "Liberation Mono", "Monospace"),
}

# Families worth offering in the picker: they cover text the app shows.
_TEXT_SYSTEMS = (
    QFontDatabase.WritingSystem.Latin,
    QFontDatabase.WritingSystem.SimplifiedChinese,
    QFontDatabase.WritingSystem.TraditionalChinese,
    QFontDatabase.WritingSystem.Japanese,
)


def current_platform() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def build_font_stack(
    platform: str,
    language: str,
    *,
    user_family: str = AUTO_FONT,
    system_family: str = "",
    available: Callable[[str], bool] = lambda _name: True,
) -> list[str]:
    """Ordered font families for the interface.

    ``language`` decides which CJK fonts come first: Japanese text drawn with
    a Chinese font shows Chinese glyph shapes for shared kanji, and the other
    way round.  Families that are not installed are left out, so Qt never
    has to search for them (macOS logs "missing font family" and spends
    time populating aliases when it does).
    """

    fonts = _PLATFORM_FONTS.get(platform, _PLATFORM_FONTS["linux"])
    cjk = ("ja", "zh") if language == "ja" else ("zh", "ja")
    candidates: list[str] = []
    if user_family and available(user_family):
        candidates.append(user_family)
    if system_family:
        candidates.append(system_family)  # what Qt itself uses by default
    # One font per group: the first installed one.  Later candidates only
    # stand in for a missing first choice; the system fallback covers the rest.
    for key in ("latin", *cjk):
        found = next((name for name in fonts[key] if available(name)), "")
        if found:
            candidates.append(found)
    return _unique(candidates)


def _unique(names: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result = []
    for name in names:
        key = name.casefold()
        if name and key not in seen:
            seen.add(key)
            result.append(name)
    return result


def system_ui_family() -> str:
    return QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family()


def font_available(family: str) -> bool:
    return bool(family) and (QFontDatabase.hasFamily(family) or family == system_ui_family())


def user_font_family() -> str:
    return str(app_config.ui_font_family or "").strip()


def interface_font_families() -> list[str]:
    """The stack for the saved choice, the running platform and UI language."""

    return build_font_stack(
        current_platform(),
        current_language(),
        user_family=user_font_family(),
        system_family=system_ui_family(),
        available=font_available,
    )


def apply_app_font(app: QApplication | None = None) -> list[str]:
    """Hand the stack to qfluentwidgets and the application font.

    Widgets already built keep their fonts; callers rebuild the window.
    """

    from qfluentwidgets import setFontFamilies

    app = app or QApplication.instance()
    families = interface_font_families()
    setFontFamilies(families)
    if app is not None:
        font = QFont(app.font())
        font.setFamilies(families)
        app.setFont(font)
    _log.info("Interface fonts: %s", ", ".join(families))
    return families


def selectable_font_families() -> list[str]:
    """Installed families for the picker, without vertical or symbol-only fonts."""

    result = []
    for family in QFontDatabase.families():
        if family.startswith(("@", ".")) or QFontDatabase.isPrivateFamily(family):
            continue
        systems = QFontDatabase.writingSystems(family)
        if any(system in systems for system in _TEXT_SYSTEMS):
            result.append(family)
    return sorted(_unique(result), key=str.casefold)


def monospace_font(point_size: int = 9) -> QFont:
    """Log-panel font: the platform's code font, then the system fixed font."""

    names = [name for name in _MONO_FONTS.get(current_platform(), ()) if QFontDatabase.hasFamily(name)]
    fixed = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    font = QFont(fixed)
    font.setFamilies(_unique(names + [fixed.family()]))
    font.setPointSize(point_size)
    font.setStyleHint(QFont.StyleHint.Monospace)
    return font
