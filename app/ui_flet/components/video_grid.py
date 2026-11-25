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
from .status_badge import BadgeKind
from .video_card import VideoCard, text_height

END_MARGIN = 600  # px before the end that count as "reached"


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
):
    shell = use_shell()
    state = shell.state.layout
    # ``width``: the grid sits in a pane narrower than the page body (list-detail).
    body = state.body_width(max_width) if width is None else min(width, state.body_width(max_width))
    metrics = layout.grid_metrics(body, state.width_class, text_height=text_height())
    asked_at, set_asked_at = ft.use_state(-1)
    badges = badges or {}

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
            VideoCard(
                card,
                metrics.tile_width,
                shell.page,
                selected=card.key in selected,
                selecting=selecting,
                touch=state.is_touch,
                badge=badges.get(card.key),
                on_open=on_open,
                on_toggle=on_toggle,
                menu=menu,
                key=card.key,
            )
            for card in cards
        ],
    )
