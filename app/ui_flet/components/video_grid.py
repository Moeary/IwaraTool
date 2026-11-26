"""``VideoGrid`` (docs/flet_ui_plan.md 7.0, 6.6): the cover grid with selection and load-on-scroll.

Columns and tile size come from ``layout.grid_metrics``; the grid builds only
the visible tiles.  Reaching the last screen calls ``on_end_reached`` once per
length of the list (the caller loads the next page).
"""
from __future__ import annotations

from collections.abc import Callable

import flet as ft

from ...presentation.media.cards import CardView
from .. import layout
from ..stores.app_state import use_shell
from ..theme import palette_for
from .status_badge import BadgeKind
from .video_card import VideoCard, text_height

END_MARGIN = 600  # px before the end that count as "reached"

# Cards are memoized: loading the next page builds only the new cards, not every card shown so far
# (P9: thousands of cards took seconds per page).  Callers pass fresh lambdas and menus on every render,
# so the cards get stable stand-ins that call the latest ones.
MemoVideoCard = ft.memo(VideoCard)


class _LatestMenu:
    """Stands in for the caller's current ``VideoMenu`` (same object across renders)."""

    def __init__(self, ref):
        self._ref = ref

    async def actions(self, card, **kwargs):
        return await self._ref.current["menu"].actions(card, **kwargs)

    async def run(self, card, action):
        return await self._ref.current["menu"].run(card, action)


def cover_size_for(shell, scale: float = 1.0) -> float | None:
    """The shared cover width on the desktop; phones keep their adaptive columns."""
    return shell.state.cover_size * scale if shell.caps.desktop else None


@ft.component
def VideoGrid(
    cards: list[CardView],
    *,
    selected: frozenset[str] = frozenset(),
    selecting: bool = False,
    badges: dict[str, tuple[BadgeKind, str | None]] | None = None,
    on_open: Callable[[CardView], None] | None = None,
    on_toggle: Callable[[CardView], None] | None = None,
    on_end_reached: Callable[[], None] | None = None,
    max_width: float = layout.MAX_WIDTH_GRID,
    menu=None,
    width: float | None = None,
    cover_scale: float = 1.0,  # card_size.PANE_SCALE in a list-detail pane
):
    shell = use_shell()
    state = shell.state.layout
    # ``width``: the grid sits in a pane narrower than the page body (list-detail).
    body = state.body_width(max_width) if width is None else min(width, state.body_width(max_width))
    metrics = layout.grid_metrics(body, state.width_class, text_height=text_height(), cover_size=cover_size_for(shell, cover_scale))
    asked_at, set_asked_at = ft.use_state(-1)
    badges = badges or {}
    latest = ft.use_ref(None)
    latest.current = {"open": on_open, "toggle": on_toggle, "menu": menu}
    stable = ft.use_ref(None)
    if stable.current is None:
        stable.current = (lambda card: latest.current["open"](card), lambda card: latest.current["toggle"](card),
                          _LatestMenu(latest))
    open_card, toggle_card, menu_card = stable.current
    look = (palette_for(shell.page), shell.state.language)  # a theme or language change rebuilds the cards

    def scrolled(e: ft.OnScrollEvent) -> None:
        if on_end_reached is None or asked_at == len(cards):
            return
        if e.max_scroll_extent and e.pixels >= e.max_scroll_extent - END_MARGIN:
            set_asked_at(len(cards))
            on_end_reached()

    return ft.GridView(
        expand=True,
        runs_count=metrics.columns,
        child_aspect_ratio=metrics.child_aspect_ratio,
        spacing=metrics.gap,
        run_spacing=metrics.gap,
        build_controls_on_demand=True,
        on_scroll=scrolled if on_end_reached else None,
        scroll_interval=100,
        controls=[
            MemoVideoCard(
                card,
                metrics.tile_width,
                shell.page,
                selected=card.key in selected,
                selecting=selecting,
                touch=state.is_touch,
                badge=badges.get(card.key),
                on_open=open_card if on_open else None,
                on_toggle=toggle_card if on_toggle else None,
                menu=menu_card if menu else None,
                look=look,
                key=card.key,
            )
            for card in cards
        ],
    )
