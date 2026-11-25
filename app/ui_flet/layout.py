"""Window size classes, shell layout and cover-grid maths (docs/flet_ui_plan.md 6.2, 6.3, 6.6).

Framework-neutral: no Flet import, so it is unit-tested directly.  The shell
feeds it the page size and platform; components read only ``LayoutState``
and never ``page.width`` or their own pixel breakpoints.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .tokens import Size

# Material 3 window size classes.
WIDTH_COMPACT = "compact"
WIDTH_MEDIUM = "medium"
WIDTH_EXPANDED = "expanded"
WIDTH_LARGE = "large"
WIDTH_XLARGE = "xlarge"
WIDTH_CLASSES = (WIDTH_COMPACT, WIDTH_MEDIUM, WIDTH_EXPANDED, WIDTH_LARGE, WIDTH_XLARGE)

HEIGHT_COMPACT = "compact"
HEIGHT_MEDIUM = "medium"
HEIGHT_EXPANDED = "expanded"

# For ResponsiveRow(breakpoints=...) so its columns follow the same classes.
BREAKPOINTS = {WIDTH_COMPACT: 0, WIDTH_MEDIUM: 600, WIDTH_EXPANDED: 840, WIDTH_LARGE: 1200, WIDTH_XLARGE: 1600}

# Shell navigation (6.3).
NAV_BAR = "bar"  # bottom NavigationBar (compact width, normal height)
NAV_RAIL_COLLAPSED = "rail_collapsed"  # icon-only rail (compact height, e.g. phone landscape)
NAV_RAIL = "rail"  # rail with labels (medium, expanded)
NAV_RAIL_EXTENDED = "rail_extended"  # wide rail with labels beside icons (large, xlarge)

# Content max widths on very wide windows (6.4).
MAX_WIDTH_GRID = 1600
MAX_WIDTH_FORM = Size.FORM_MAX_WIDTH
MAX_WIDTH_LIST_DETAIL = 1440
LIST_DETAIL_MIN_WIDTH = 840  # two panes from here on

# Rail rows (measured on the Flet 1.1 client): a destination with its label
# under the icon, one with the label beside it, and what the rail needs besides
# (leading control, group gap, account button at the bottom).
RAIL_ROW = {NAV_RAIL: 66, NAV_RAIL_EXTENDED: 48, NAV_RAIL_COLLAPSED: 56}
RAIL_CHROME = {NAV_RAIL: 136, NAV_RAIL_EXTENDED: 176, NAV_RAIL_COLLAPSED: 136}

DESKTOP = frozenset({"windows", "macos", "linux"})
MOBILE = frozenset({"android", "ios"})


def width_class(width: float) -> str:
    if width < 600:
        return WIDTH_COMPACT
    if width < 840:
        return WIDTH_MEDIUM
    if width < 1200:
        return WIDTH_EXPANDED
    if width < 1600:
        return WIDTH_LARGE
    return WIDTH_XLARGE


def height_class(height: float) -> str:
    if height < 480:
        return HEIGHT_COMPACT
    if height < 900:
        return HEIGHT_MEDIUM
    return HEIGHT_EXPANDED


@dataclass(frozen=True, slots=True)
class Insets:
    left: float = 0
    top: float = 0
    right: float = 0
    bottom: float = 0


@dataclass(frozen=True, slots=True)
class LayoutState:
    width: float
    height: float
    platform: str = "macos"  # lower-case PagePlatform value
    safe_insets: Insets = field(default_factory=Insets)
    keyboard_inset: float = 0
    rail_extended_preference: bool = True  # the user can collapse the wide rail; remembered

    @property
    def width_class(self) -> str:
        return width_class(self.width)

    @property
    def height_class(self) -> str:
        return height_class(self.height)

    @property
    def orientation(self) -> str:
        return "landscape" if self.width > self.height else "portrait"

    @property
    def aspect(self) -> float:
        """Long side ÷ short side (21:9 phones are about 2.3)."""
        short = min(self.width, self.height) or 1
        return max(self.width, self.height) / short

    @property
    def is_mobile(self) -> bool:
        return self.platform in MOBILE

    @property
    def is_touch(self) -> bool:
        return self.is_mobile

    @property
    def is_desktop(self) -> bool:
        return not self.is_mobile

    # ── shell ────────────────────────────────────────────────────────────────

    @property
    def nav_mode(self) -> str:
        # A compact height wins over the width (21:9 landscape is ~900 wide but ~400 tall).
        if self.height_class == HEIGHT_COMPACT:
            return NAV_RAIL_COLLAPSED
        if self.width_class == WIDTH_COMPACT:
            return NAV_BAR
        if self.width_class in (WIDTH_LARGE, WIDTH_XLARGE) and self.rail_extended_preference:
            return NAV_RAIL_EXTENDED
        return NAV_RAIL

    @property
    def nav_width(self) -> float:
        """Width the navigation takes beside the content (0 for the bottom bar)."""
        mode = self.nav_mode
        if mode == NAV_BAR:
            return 0
        if mode == NAV_RAIL_EXTENDED:
            return Size.NAV_RAIL_EXTENDED
        return Size.NAV_RAIL

    def rail_fits(self, count: int) -> bool:
        """Whether ``count`` destinations fit the rail's height without scrolling."""
        mode = self.nav_mode
        if mode == NAV_BAR:
            return False
        usable = self.height - self.safe_insets.top - self.safe_insets.bottom
        return count * RAIL_ROW[mode] + RAIL_CHROME[mode] <= usable

    @property
    def content_width(self) -> float:
        divider = 0 if self.nav_mode == NAV_BAR else 1
        return max(0.0, self.width - self.nav_width - divider - self.safe_insets.left - self.safe_insets.right)

    @property
    def two_pane(self) -> bool:
        """List-detail pages show both panes (6.4)."""
        return self.content_width >= LIST_DETAIL_MIN_WIDTH and self.height_class != HEIGHT_COMPACT

    @property
    def page_padding(self) -> float:
        return 16 if self.width_class == WIDTH_COMPACT else 24 if self.width_class == WIDTH_MEDIUM else 32

    def body_width(self, max_width: float = MAX_WIDTH_GRID) -> float:
        """Width inside a ``PageFrame``: the content, capped at ``max_width``, less the page padding."""
        return max(1.0, min(self.content_width, max_width) - 2 * self.page_padding)

    def shell_key(self) -> tuple:
        """What the shell's structure depends on; rebuild only when this changes."""
        return (self.nav_mode, self.width_class, self.height_class, self.is_mobile)


# ── cover grid (6.6) ─────────────────────────────────────────────────────────

_GRID = {
    WIDTH_COMPACT: (12, 150, 2, 3),
    WIDTH_MEDIUM: (16, 180, 3, 5),
    WIDTH_EXPANDED: (16, 200, 3, 6),
    WIDTH_LARGE: (20, 220, 4, 8),
    WIDTH_XLARGE: (20, 220, 4, 8),
}
DENSITY_SCALE = {"compact": 0.85, "standard": 1.0, "large": 1.4}
COVER_SIZE_RANGE = (160, 420)  # desktop slider (min card width)


@dataclass(frozen=True, slots=True)
class GridMetrics:
    columns: int
    gap: float
    tile_width: float
    child_aspect_ratio: float  # tile width ÷ tile height, for GridView


def grid_metrics(
    available_width: float,
    size_class: str,
    *,
    text_height: float,
    density: str = "standard",
    cover_size: float | None = None,
) -> GridMetrics:
    """Columns and tile shape for a 16:9 cover + ``text_height`` caption.

    ``cover_size`` (desktop slider, min card width) overrides the density.
    """

    gap, min_card, min_cols, max_cols = _GRID.get(size_class, _GRID[WIDTH_EXPANDED])
    if size_class == WIDTH_COMPACT and available_width < 360:
        gap = 8
    if cover_size is not None:
        low, high = COVER_SIZE_RANGE
        min_card = min(max(float(cover_size), low), high)
    else:
        min_card *= DENSITY_SCALE.get(density, 1.0)
    if size_class == WIDTH_COMPACT and density == "large" and cover_size is None:
        min_cols = 1  # "large" on a phone is one big card per row
    avail = max(1.0, float(available_width))
    columns = math.floor((avail + gap) / (min_card + gap))
    columns = max(min_cols, min(max_cols, columns))
    tile = (avail - gap * (columns - 1)) / columns
    height = tile * 9 / 16 + text_height
    return GridMetrics(columns=columns, gap=gap, tile_width=tile, child_aspect_ratio=tile / height)


def caption_height(*, title_line: float, meta_line: float, padding: float, title_lines: int = 2) -> float:
    """Card text area: padding + title lines + one meta line (scales with the font size)."""
    return padding * 2 + title_line * title_lines + meta_line
