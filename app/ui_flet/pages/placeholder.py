"""Stand-in for a destination that has not been ported yet."""
from __future__ import annotations

import flet as ft

from ...i18n import tr
from .. import routes
from ..components.page_frame import PageFrame
from ..icons import AppIcon
from ..theme import text_style
from ..tokens import TYPE, Size, Space


@ft.component
def PlaceholderPage(destination: str, path: str):
    route = routes.parse(path)
    title = routes.label(route.destination if route.destination in (routes.DETAIL, routes.AUTHOR) else destination)
    body = ft.Container(
        alignment=ft.Alignment.CENTER,
        expand=True,
        content=ft.Column(
            tight=True,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=Space.MD,
            controls=[
                ft.Icon(AppIcon.EMPTY, size=Size.AVATAR),
                ft.Text(
                    tr("This page has not been ported yet.", "此页面尚在移植中。", "このページは移植中です。"),
                    style=text_style(TYPE.body),
                    text_align=ft.TextAlign.CENTER,
                ),
                ft.Text(path, style=text_style(TYPE.meta), selectable=True),
            ],
        ),
    )
    return PageFrame(title=title, body=body)
