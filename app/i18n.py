"""Lightweight i18n helpers (English/Chinese/Japanese)."""
from __future__ import annotations

from typing import Literal

from .config import app_config


def current_language() -> Literal["en", "zh", "ja"]:
    """Current UI language from app settings.

    Default is Chinese to keep UI consistent for first release.
    """
    raw = (app_config.ui_language or "zh_CN").lower()
    if raw.startswith("zh"):
        return "zh"
    if raw.startswith("ja") or raw.startswith("jp"):
        return "ja"
    return "en"


def tr(en_text: str, zh_text: str, ja_text: str | None = None) -> str:
    """Return translated text by current language.

    English is the source/base language. Japanese falls back to English when
    not provided.
    """
    lang = current_language()
    if lang == "zh":
        return zh_text
    if lang == "ja":
        return ja_text if ja_text is not None else en_text
    return en_text
