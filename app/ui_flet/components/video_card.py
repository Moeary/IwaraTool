"""``VideoCard`` (docs/flet_ui_plan.md 7.0): cover with duration and state, two-line title, author, one meta line.

The same card for videos, image posts, authors (round avatar) and playlists.
A tick appears top-right while selecting, or on hover with a mouse; a long
press (touch) or the tick starts selecting.  With a ``menu`` (``VideoActions``)
a right click opens the unified action menu; on touch a long press opens the
action sheet, whose "Select" entry starts selecting.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import flet as ft

from ...presentation.media.cards import KIND_AUTHOR, KIND_IMAGE, KIND_PLAYLIST, CardView
from .. import layout
from ..icons import AppIcon, StateIcon
from ..theme import palette_for, text_style
from ..tokens import TYPE, Radius, Size, Space
from .cover import Cover
from .status_badge import BadgeKind, status_badge

if TYPE_CHECKING:
    from .video_actions import VideoMenu

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
    menu: "VideoMenu | None" = None,
    look: object = None,  # unused here: VideoGrid passes (palette, language) so memoized cards rebuild on a change
):
    hovered, set_hovered = ft.use_state(False)
    menu_ref = ft.use_ref(None)
    request, set_request = ft.use_state(None)  # (actions, position, serial): open the menu after the render

    async def open_menu_when_ready() -> None:
        if request is not None and menu_ref.current is not None:
            await menu_ref.current.open(global_position=request[1])

    ft.use_effect(open_menu_when_ready, [request[2] if request else 0])
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

    def pick(action) -> None:
        page.run_task(menu.run, card, action)

    async def context_menu(event) -> None:
        actions = await menu.actions(card)
        if actions:
            set_request((actions, event.global_position, (request[2] + 1) if request else 1))

    async def action_sheet() -> None:
        from .video_actions import open_action_sheet

        actions = await menu.actions(card, touch=True)
        if actions:
            open_action_sheet(page, card.title, actions, pick)

    async def press_menu() -> None:
        if await menu.choose_player(card):  # a downloaded file: in the app or in an external player
            return
        if touch:
            await action_sheet()
        elif card.selectable and on_toggle:
            on_toggle(card)

    def long_press(_e) -> None:
        if menu is not None and not selecting:
            page.run_task(press_menu)
        elif card.selectable and on_toggle:
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
    body = ft.Container(
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
    if menu is None or touch:
        return body
    from .video_actions import menu_items

    return ft.ContextMenu(
        ref=menu_ref,
        items=menu_items(request[0], pick) if request else [],
        secondary_trigger=None,
        tertiary_trigger=None,
        content=ft.GestureDetector(on_secondary_tap_down=lambda e: page.run_task(context_menu, e), content=body),
    )
