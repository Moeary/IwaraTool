"""App-wide preferences both interfaces switch: content rating, site, language, theme mode.

Each setter saves the choice and announces it on ``signal_bus`` the way the
PySide6 window always has, so pages of either interface follow along.
"""
from __future__ import annotations

from ..config import app_config
from ..core import site as sites
from ..core.rating import RATING_ALL, RATINGS, UI_RATING_KEY, normalize_rating, rating_label  # noqa: F401
from ..i18n import tr

LANGUAGES = ("zh", "en", "ja")
_LANGUAGE_CODES = {"zh": "zh_CN", "en": "en_US", "ja": "ja_JP"}
THEME_MODES = ("auto", "light", "dark")


def _bus():
    from ..signal_bus import signal_bus

    return signal_bus


# ── content rating ───────────────────────────────────────────────────────────

def current_rating() -> str:
    return normalize_rating(app_config.get_ui_value(UI_RATING_KEY, RATING_ALL))


def set_rating(value: object) -> str:
    rating = normalize_rating(value)
    app_config.set_ui_value(UI_RATING_KEY, rating)
    _bus().content_rating_changed.emit(rating)
    return rating


def cycle_rating() -> str:
    """All → SFW → NSFW → All."""
    current = current_rating()
    return set_rating(RATINGS[(RATINGS.index(current) + 1) % len(RATINGS)])


# ── site ─────────────────────────────────────────────────────────────────────

def current_site() -> str:
    return sites.configured_site()


def set_site(key: str) -> bool:
    return sites.set_configured_site(key)


def site_label(key: str) -> str:
    return sites.SITES[sites.normalize(key)].label


def site_switched_text(key: str) -> str:
    label = site_label(key)
    return tr(f"Now browsing {label}", f"已切换到 {label}", f"{label} に切り替えました")


# ── language ─────────────────────────────────────────────────────────────────

def language_label(language: str) -> str:
    return {"zh": "简体中文", "en": "English", "ja": "日本語"}.get(language, language)


def set_language(language: str) -> str:
    """Save ``zh`` / ``en`` / ``ja`` (anything else means Chinese) and announce it."""
    code = _LANGUAGE_CODES.get(str(language or "").strip().lower()[:2], "zh_CN")
    app_config.ui_language = code
    _bus().language_changed.emit(code)
    return code


# ── theme ────────────────────────────────────────────────────────────────────

def normalize_theme_mode(value: object) -> str:
    mode = str(value or "").strip().lower()
    return mode if mode in THEME_MODES else "auto"


def theme_mode_label(mode: str) -> str:
    return {
        "auto": tr("Follow system", "跟随系统", "システムに合わせる"),
        "light": tr("Light", "浅色", "ライト"),
        "dark": tr("Dark", "深色", "ダーク"),
    }[normalize_theme_mode(mode)]


def set_theme_mode(value: object) -> str:
    """Save the theme mode; each interface applies it itself."""
    mode = normalize_theme_mode(value)
    app_config.theme_mode = mode
    return mode
