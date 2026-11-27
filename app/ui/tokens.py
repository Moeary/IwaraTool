"""Design tokens for the Flet interface, as plain data.

Every color, size, radius and duration a page uses comes from here; pages
never write hex values or pixel sizes of their own.  ``theme.py`` turns these
tokens into a Flet ``Theme``.  This module imports no UI framework so the
tokens can be unit tested (contrast, scale) in any environment.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Literal

Mode = Literal["light", "dark"]

BRAND_SEED = "#00848E"  # IwaraTool teal; Material derives the unlisted roles from it


@dataclass(frozen=True)
class Semantic:
    """A status color: ``fg`` for text and icons, ``bg`` for the badge or banner behind them."""

    fg: str
    bg: str


@dataclass(frozen=True)
class Palette:
    """Color roles for one brightness.

    Surface ladder, with the Material 3 role each maps to in ``theme.py``:

    ============  ==========================  ================================
    token         Material role               used by
    ============  ==========================  ================================
    surface       surface                     page background, navigation rail
    lowest        surface_container_lowest    wells sunk below the page
    card          surface_container_low       cards, panels
    container     surface_container           bottom bar, menus
    raised        surface_container_high      dialogs, sheets
    field         surface_container_highest   inputs, chips, skeletons
    ============  ==========================  ================================
    """

    primary: str
    on_primary: str
    primary_container: str
    on_primary_container: str
    surface: str
    lowest: str
    card: str
    container: str
    raised: str
    field: str
    outline: str
    outline_variant: str
    on_surface: str
    on_surface_variant: str
    disabled: str
    scrim: str
    inverse_surface: str
    on_inverse_surface: str
    inverse_primary: str
    cover_placeholder: str
    cover_scrim: str  # behind text drawn on a cover (duration, badges)
    on_cover_scrim: str  # text and icons on cover_scrim
    success: Semantic
    warning: Semantic
    danger: Semantic
    info: Semantic


LIGHT = Palette(
    primary="#007A84",
    on_primary="#FFFFFF",
    primary_container="#D9EEF0",
    on_primary_container="#0B2F33",
    surface="#F6F7F9",
    lowest="#FFFFFF",
    card="#FFFFFF",
    container="#F0F2F5",
    raised="#FFFFFF",
    field="#EEF1F4",
    outline="#8A9096",
    outline_variant="#E1E4E8",
    on_surface="#1B1B1B",
    on_surface_variant="#5F6368",
    disabled="#A0A4A8",
    scrim="#000000",
    inverse_surface="#2E3134",
    on_inverse_surface="#F1F2F4",
    inverse_primary="#4CC2C9",
    cover_placeholder="#E8EBEF",
    cover_scrim="#B3000000",
    on_cover_scrim="#FFFFFF",
    success=Semantic(fg="#0F7B0F", bg="#DFF3E2"),
    warning=Semantic(fg="#9D5D00", bg="#FFF1D6"),
    danger=Semantic(fg="#C42B1C", bg="#FDE7E4"),
    info=Semantic(fg="#005FB8", bg="#E1EEFB"),
)

DARK = Palette(
    primary="#4CC2C9",
    on_primary="#00363A",
    primary_container="#2C4B4D",
    on_primary_container="#FFFFFF",
    surface="#111315",
    lowest="#0C0E10",
    card="#1B1E21",
    container="#1F2225",
    raised="#22262A",
    field="#25292D",
    outline="#6C7278",
    outline_variant="#33383D",
    on_surface="#F3F3F3",
    on_surface_variant="#B4B8BC",
    disabled="#6C7075",
    scrim="#000000",
    inverse_surface="#E3E5E8",
    on_inverse_surface="#1B1E21",
    inverse_primary="#006B73",
    cover_placeholder="#25292D",
    cover_scrim="#B3000000",
    on_cover_scrim="#FFFFFF",
    success=Semantic(fg="#6CCB5F", bg="#1E3A22"),
    warning=Semantic(fg="#F0B44C", bg="#3D2E12"),
    danger=Semantic(fg="#FF99A4", bg="#47201F"),
    info=Semantic(fg="#60CDFF", bg="#14304A"),
)


def palette(mode: Mode) -> Palette:
    return DARK if mode == "dark" else LIGHT


# ── Spacing, radius, elevation ───────────────────────────────────────────────


class Space:
    """Spacing on a 4 dp grid."""

    XXS = 2
    XS = 4
    SM = 8
    MD = 12
    LG = 16
    XL = 24
    XXL = 32


class Radius:
    BADGE = 6
    CHIP = 8
    CONTROL = 10  # buttons, inputs, menus, list rows, snack bars
    CARD = 12  # cards and covers
    FAB = 16
    DIALOG = 24
    SHEET = 28  # top corners of bottom sheets
    FULL = 999


class Size:
    """Fixed component sizes (logical pixels)."""

    TOUCH_TARGET = 48  # minimum on touch devices
    ROW_DESKTOP = 40  # list row height with a mouse
    ICON_SM = 16
    ICON = 20
    ICON_LG = 24
    ICON_NAV = 24
    AVATAR_SM = 28
    AVATAR = 40
    NAV_RAIL = 72
    NAV_RAIL_EXTENDED = 248
    NAV_BAR = 64
    NAV_INDICATOR_WIDTH = 56  # the rail's selection pill (Material 3)
    NAV_INDICATOR_HEIGHT = 32
    NAV_DRAWER_ITEM = 56  # a navigation drawer row (Material 3)
    SETTINGS_LABEL = 96  # the label column of key-value rows (About)
    PROGRESS_BAR = 3  # the bar along the bottom of a downloading cover
    DIALOG_MAX_WIDTH = 560
    FORM_MAX_WIDTH = 720
    LIST_DETAIL_MAX_WIDTH = 1440
    GRID_MAX_WIDTH = 1600


class Motion:
    """Durations in milliseconds; all use the standard easing curve."""

    FAST = 150
    NORMAL = 200
    SLOW = 250
    COVER_FADE = 180
    HOVER_LIFT = 2  # dp a card rises on desktop hover


# ── Typography ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TypeRole:
    size: float
    weight: int
    line_height: float  # multiple of size; CJK needs more room than Latin


@dataclass(frozen=True)
class TypeScale:
    """Text roles, mapped onto Material roles in ``theme.py``.

    Page and section titles shrink on compact widths; pick the ``*_compact``
    role there through ``LayoutState``, never by measuring the page.
    """

    page_title: TypeRole
    page_title_compact: TypeRole
    section_title: TypeRole
    section_title_compact: TypeRole
    subtitle: TypeRole  # list item titles, dialog headings below the title
    card_title: TypeRole
    body: TypeRole
    label: TypeRole  # buttons, tabs, chips
    meta: TypeRole  # secondary line: author · views · date
    badge: TypeRole


TYPE = TypeScale(
    page_title=TypeRole(28, 600, 1.25),
    page_title_compact=TypeRole(22, 600, 1.27),
    section_title=TypeRole(20, 600, 1.3),
    section_title_compact=TypeRole(18, 600, 1.33),
    subtitle=TypeRole(16, 500, 1.4),
    card_title=TypeRole(14, 500, 1.43),
    body=TypeRole(14, 400, 1.5),
    label=TypeRole(14, 500, 1.43),
    meta=TypeRole(12, 400, 1.4),
    badge=TypeRole(11, 600, 1.3),
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def color_fields(p: Palette) -> dict[str, str]:
    """Every plain color of a palette by role name (semantic pairs flattened)."""

    result: dict[str, str] = {}
    for f in fields(p):
        value = getattr(p, f.name)
        if isinstance(value, Semantic):
            result[f"{f.name}.fg"] = value.fg
            result[f"{f.name}.bg"] = value.bg
        else:
            result[f.name] = value
    return result


def _channel(value: int) -> float:
    c = value / 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(color: str) -> float:
    """WCAG relative luminance of ``#RRGGBB`` (or the RGB part of ``#AARRGGBB``)."""

    text = color.lstrip("#")
    if len(text) == 8:
        text = text[2:]
    r, g, b = (int(text[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast_ratio(a: str, b: str) -> float:
    hi, lo = sorted((relative_luminance(a), relative_luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def blend(over: str, under: str) -> str:
    """``#AARRGGBB`` ``over`` composited on opaque ``under``, as ``#RRGGBB``."""

    text = over.lstrip("#")
    alpha = int(text[:2], 16) / 255 if len(text) == 8 else 1.0
    top = text[2:] if len(text) == 8 else text
    base = under.lstrip("#")[-6:]
    channels = (
        round(int(top[i:i + 2], 16) * alpha + int(base[i:i + 2], 16) * (1 - alpha)) for i in (0, 2, 4)
    )
    return "#" + "".join(f"{value:02X}" for value in channels)
