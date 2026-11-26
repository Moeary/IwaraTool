"""Platform font stacks for the interface, without any UI framework.

Both interfaces draw text from an ordered family list: a Latin UI font, then
the Chinese and Japanese UI fonts in the order the interface language needs.
Qt asks its font database which families are installed; Flutter (Flet) has
no such query and simply skips families it cannot find, so it passes an
``available`` that accepts every name.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterable

AUTO_FONT = ""  # ``ui_font_family`` value meaning "follow the platform"

# Per platform: Latin UI font, then Simplified Chinese and Japanese UI fonts.
# The system UI font the toolkit reports is put in front of the Latin list at runtime.
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
        candidates.append(system_family)  # what the toolkit itself uses by default
    # One font per group: the first installed one.  Later candidates only
    # stand in for a missing first choice; the system fallback covers the rest.
    for key in ("latin", *cjk):
        found = next((name for name in fonts[key] if available(name)), "")
        if found:
            candidates.append(found)
    return _unique(candidates)


def cjk_fallback(platform: str, language: str) -> list[str]:
    """Every CJK family for ``platform``, the interface language's script first.

    For toolkits that cannot ask which fonts are installed: the whole list is
    handed over and missing families are skipped while drawing.
    """

    fonts = _PLATFORM_FONTS.get(platform, _PLATFORM_FONTS["linux"])
    order = ("ja", "zh") if language == "ja" else ("zh", "ja")
    return _unique(name for key in order for name in fonts[key])


def monospace_families(platform: str) -> list[str]:
    return list(_MONO_FONTS.get(platform, _MONO_FONTS["linux"]))


def _unique(names: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result = []
    for name in names:
        key = name.casefold()
        if name and key not in seen:
            seen.add(key)
            result.append(name)
    return result
