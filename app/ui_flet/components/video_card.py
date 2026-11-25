"""``VideoCard`` (docs/flet_ui_plan.md 7.0): cover with duration and state, two-line title, author, one meta line.

The same card for videos, image posts, authors (round avatar) and playlists.
A tick appears top-right while selecting, or on hover with a mouse; a long
press (touch) or the tick starts selecting.
"""
from __future__ import annotations

from collections.abc import Callable

import flet as ft

from ...presentation.media.cards import KIND_AUTHOR, KIND_IMAGE, KIND_PLAYLIST, CardView
from .. import layout
from ..icons import AppIcon, StateIcon
from ..theme import palette_for, text_style
from ..tokens import TYPE, Radius, Size, Space
from .cover import Cover
from .status_badge import BadgeKind, status_badge

CARD_PADDING = Space.SM


def text_height() -> float:
    """The card's text area, for ``grid_metrics``: two title lines, author and meta."""
    return layout.caption_height(
        title_line=TYPE.card_title.size * TYPE.card_title.line_height,
        meta_line=TYPE.meta.size * TYPE.meta.line_height,
        padding=CARD_PADDING,
    ) + TYPE.meta.size * TYPE.meta.line_height


def _cover_icon(kind: str):
    return {KIND_AUTHOR: AppIcon.AUTHOR, KIND_PLAYLIST: AppIcon.PLAYLIST, KIND_IMAGE: AppIcon.IMAGE}.get(kind, AppIcon.VIDEO)


@ft.component
def VideoCard(
    card: CardView,
    width: float,
    page: ft.Page,
    *,
    selected: bool = False,
    selecting: bool = False,
    touch: bool = False,
    badge: tuple[BadgeKind, str | None] | None = None,
    on_open: Callable[[CardView], None] | None = None,
    on_toggle: Callable[[CardView], None] | None = None,
):
    hovered, set_hovered = ft.use_state(False)
    palette = palette_for(page)
    overlays: list[ft.Control] = [
        Cover(card.cover_kind, card.cover_key, card.cover_url, width, card.cover_local,
              round=card.round_cover, icon=_cover_icon(card.kind), radius=Radius.CARD),
    ]
    if card.duration:
        overlays.append(ft.Container(
            right=Space.SM,
            bottom=Space.SM,
            padding=ft.Padding.symmetric(horizontal=Space.XS + 2, vertical=Space.XXS),
            border_radius=Radius.BADGE,
            bgcolor=palette.cover_scrim,
            content=ft.Text(card.duration, style=text_style(TYPE.meta, color=palette.on_cover_scrim)),
        ))
    if badge is not None:
        overlays.append(ft.Container(left=Space.SM, top=Space.SM, content=status_badge(badge[0], palette, text=badge[1])))
    show_tick = card.selectable and (selecting or selected or (hovered and not touch))
    if show_tick:
        icon = StateIcon.SELECT.active if selected else StateIcon.SELECT.rest
        overlays.append(ft.Container(
            right=Space.XS,
            top=Space.XS,
            border_radius=Radius.FULL,
            bgcolor=palette.cover_scrim,
            content=ft.IconButton(
                icon=icon,
                icon_color=palette.primary if selected else palette.on_cover_scrim,
                icon_size=Size.ICON_LG,
                on_click=lambda _e: on_toggle and on_toggle(card),
            ),
        ))

    def click(_e) -> None:
        if selecting and card.selectable:
            on_toggle and on_toggle(card)
        elif on_open:
            on_open(card)

    def long_press(_e) -> None:
        if card.selectable and on_toggle:
            on_toggle(card)

    text = ft.Column(
        spacing=0,
        controls=[
            ft.Text(card.title, style=text_style(TYPE.card_title), max_lines=2, overflow=ft.TextOverflow.ELLIPSIS,
                    height=TYPE.card_title.size * TYPE.card_title.line_height * 2),
            ft.Text(card.subtitle, style=text_style(TYPE.meta, color=palette.on_surface_variant), max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS),
            ft.Text(card.meta, style=text_style(TYPE.meta, color=palette.on_surface_variant), max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS),
        ],
    )
    return ft.Container(
        width=width,
        border_radius=Radius.CARD,
        bgcolor=palette.primary_container if selected else None,
        on_click=click,
        on_long_press=long_press,
        on_hover=None if touch else (lambda e: set_hovered(str(e.data).lower() == "true")),
        tooltip=card.tooltip or None,
        content=ft.Column(
            spacing=0,
            controls=[
                ft.Stack(controls=overlays),
                ft.Container(padding=ft.Padding.symmetric(horizontal=Space.XS, vertical=CARD_PADDING), content=text),
            ],
        ),
    )
