"""Lightweight i18n helpers (English/Chinese/Japanese)."""
from __future__ import annotations

import functools
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Literal, ParamSpec, TypeVar

from .config import app_config

Language = Literal["en", "zh", "ja"]
P = ParamSpec("P")
R = TypeVar("R")

_pinned = threading.local()


def current_language() -> Language:
    """Current UI language from app settings.

    Default is Chinese to keep UI consistent for first release.
    """
    pinned = getattr(_pinned, "language", None)
    if pinned:
        return pinned
    raw = (app_config.ui_language or "zh_CN").lower()
    if raw.startswith("zh"):
        return "zh"
    if raw.startswith("ja") or raw.startswith("jp"):
        return "ja"
    return "en"


@contextmanager
def pinned_language(language: Language | None = None) -> Iterator[Language]:
    """Translate with one language for a burst of work on this thread.

    Every ``tr`` otherwise reads the settings store under its lock. Worker
    threads translating per file contend on that lock, and each wait also
    re-queues for the GIL, which stalled the GUI thread for seconds. Pass the
    ``language`` resolved by the starting thread to pin pool threads to it.
    """
    previous = getattr(_pinned, "language", None)
    _pinned.language = language or current_language()
    try:
        yield _pinned.language
    finally:
        _pinned.language = previous


def pin_language(func: Callable[P, R]) -> Callable[P, R]:
    """Decorator: run ``func`` under ``pinned_language()``."""

    @functools.wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        with pinned_language():
            return func(*args, **kwargs)

    return wrapper


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
