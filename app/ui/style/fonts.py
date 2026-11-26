"""Interface fonts: a platform-adaptive base stack, optionally led by the user's pick.

The stack is an ordered family list.  Qt (both ``QFont.setFamilies`` and the
QSS ``font-family`` list that qfluentwidgets builds from ``qconfig``) looks a
glyph up family by family and finally in the system fallback, so a chosen
font that lacks Chinese or Japanese characters still shows them through the
families behind it.
"""

from __future__ import annotations

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

from ...config import app_config
from ...i18n import current_language
from ...logging_setup import get_logger
# The platform tables and the stack order live in a module without Qt, shared
# with the Flet interface; re-exported here for existing callers and tests.
from ...presentation.fonts import (  # noqa: F401
    _MONO_FONTS,
    _PLATFORM_FONTS,
    AUTO_FONT,
    _unique,
    build_font_stack,
    current_platform,
)

_log = get_logger(__name__)

# Families worth offering in the picker: they cover text the app shows.
_TEXT_SYSTEMS = (
    QFontDatabase.WritingSystem.Latin,
    QFontDatabase.WritingSystem.SimplifiedChinese,
    QFontDatabase.WritingSystem.TraditionalChinese,
    QFontDatabase.WritingSystem.Japanese,
)


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
