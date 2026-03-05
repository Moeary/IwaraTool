"""Lightweight i18n helpers (English base, Chinese optional)."""
from __future__ import annotations

from .config import app_config


def current_language() -> str:
    """Current UI language from app settings.

    Default is Chinese to keep UI consistent for first release.
    """
    raw = (app_config.ui_language or "zh_CN").lower()
    return "en" if raw.startswith("en") else "zh"


def tr(en_text: str, zh_text: str) -> str:
    """Return translated text by system language.

    English is the source/base language.
    """
    return zh_text if current_language() == "zh" else en_text
