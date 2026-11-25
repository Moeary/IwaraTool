"""In-app notices (SnackBar) with the four kinds the presentation layer uses."""
from __future__ import annotations

from collections.abc import Callable

import flet as ft

from ..icons import AppIcon
from ..theme import palette_for, text_style
from ..tokens import TYPE, Size, Space

KINDS = ("info", "success", "warning", "error")


def _icon(kind: str):
    return {"success": AppIcon.SUCCESS, "warning": AppIcon.WARNING, "error": AppIcon.ERROR}.get(kind, AppIcon.INFO)


def _color(page: ft.Page, kind: str) -> str:
    p = palette_for(page)
    role = {"success": p.success, "warning": p.warning, "error": p.danger}.get(kind, p.info)
    return role.fg


def notice(
    page: ft.Page,
    kind: str,
    title: str,
    content: str = "",
    *,
    action: str = "",
    on_action: Callable[[], None] | None = None,
    duration_ms: int = 3500,
) -> ft.SnackBar:
    texts: list[ft.Control] = [ft.Text(title, style=text_style(TYPE.label))]
    if content:
        texts.append(ft.Text(content, style=text_style(TYPE.meta), max_lines=3, overflow=ft.TextOverflow.ELLIPSIS))
    bar = ft.SnackBar(
        behavior=ft.SnackBarBehavior.FLOATING,
        show_close_icon=True,
        width=Size.DIALOG_MAX_WIDTH if (page.width or 0) > Size.DIALOG_MAX_WIDTH + 2 * Space.LG else None,
        duration=ft.Duration(milliseconds=duration_ms),
        action=action or None,
        on_action=(lambda _e: on_action()) if on_action else None,
        content=ft.Row(
            spacing=Space.MD,
            controls=[
                ft.Icon(_icon(kind), color=_color(page, kind), size=Size.ICON_LG),
                ft.Column(spacing=0, tight=True, expand=True, controls=texts),
            ],
        ),
    )
    page.show_dialog(bar)
    return bar
