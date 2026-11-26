"""The frame every page sits in: header (back, title, actions) over a width-limited body.

On phones (bottom bar) the header doubles as the app bar and carries the
account button; on wider windows the account button lives in the rail.
"""
from __future__ import annotations

import flet as ft

from ...i18n import tr
from .. import layout
from ..icons import AppIcon
from ..stores.app_state import use_shell
from ..theme import text_style
from ..tokens import TYPE, Space
from .navigation import account_button


@ft.component
def PageFrame(
    title: str,
    body: ft.Control,
    actions: list[ft.Control] | None = None,
    max_width: float = layout.MAX_WIDTH_GRID,
    scroll: bool = False,
    below: ft.Control | None = None,  # under the title row (the Tools tabs)
):
    shell = use_shell()
    state = shell.state
    # Phones, and landscape phones (little height): the smaller title and tighter header.
    compact = state.layout.width_class == layout.WIDTH_COMPACT or state.layout.height_class == layout.HEIGHT_COMPACT
    padding = state.layout.page_padding
    leading: list[ft.Control] = []
    if state.can_back:
        leading.append(
            ft.IconButton(
                icon=AppIcon.BACK,
                tooltip=tr("Back", "返回", "戻る"),
                on_click=lambda _e: shell.back(),
            )
        )
    trailing = list(actions or [])
    if state.layout.nav_mode == layout.NAV_BAR:
        trailing.append(account_button(shell))
    header = ft.Container(
        padding=ft.Padding.only(
            left=padding - (Space.SM if leading else 0),
            right=padding - Space.SM,
            top=Space.XS if compact else Space.LG,
            bottom=Space.XS if compact else Space.SM,
        ),
        content=ft.Column(spacing=Space.SM, controls=[c for c in (ft.Row(
            spacing=Space.XS,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                *leading,
                ft.Text(
                    title,
                    style=text_style(TYPE.page_title_compact if compact else TYPE.page_title),
                    max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS,
                    expand=True,
                ),
                *trailing,
            ],
        ), below) if c is not None]),
    )
    content = ft.Container(
        expand=True,
        padding=ft.Padding.only(left=padding, right=padding, bottom=padding),
        content=body,
    )
    column = ft.Column(
        expand=True,
        spacing=0,
        scroll=ft.ScrollMode.AUTO if scroll else None,
        controls=[header, content],
    )
    return ft.Container(
        expand=True,
        alignment=ft.Alignment.TOP_CENTER,
        content=ft.Container(
            expand=True,
            width=None if state.layout.content_width <= max_width else max_width,
            content=column,
        ),
    )
