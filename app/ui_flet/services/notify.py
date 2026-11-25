"""In-app notices (SnackBar) with the four kinds the presentation layer uses."""
from __future__ import annotations

from collections.abc import Callable

import flet as ft

from ..icons import AppIcon
from ..theme import palette_for, text_style
from ..tokens import TYPE, Size, Space

KINDS = ("info", "success", "warning", "error")

# The window's real width and whether a bottom bar is showing, set by the shell
# on every layout change (``page.width`` lags behind at start-up).
_FRAMES: dict[int, tuple[float, float]] = {}


def set_frame(page: ft.Page, *, width: float, bottom_inset: float) -> None:
    _FRAMES[id(page)] = (float(width), float(bottom_inset))


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
    width, bottom = _FRAMES.get(id(page), (float(page.width or 0), 0.0))
    narrow = width <= Size.DIALOG_MAX_WIDTH + 2 * Space.LG
    bar = ft.SnackBar(
        behavior=ft.SnackBarBehavior.FLOATING,
        show_close_icon=True,
        # Flutter takes either a width or a margin: phones float it above the bottom bar.
        width=None if narrow else Size.DIALOG_MAX_WIDTH,
        margin=ft.Margin.only(left=Space.SM, right=Space.SM, bottom=bottom + Space.SM) if narrow else None,
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
