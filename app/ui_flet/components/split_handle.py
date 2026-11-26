"""A draggable divider between two panes (list-detail pages)."""
from __future__ import annotations

from typing import Callable

import flet as ft

from ..tokens import Space


@ft.component
def SplitHandle(on_drag: Callable[[float], None], on_end: Callable[[], None] | None = None, *, width: float = Space.LG):
    """A vertical rule with a wide grab area; ``on_drag`` gets each horizontal move in pixels."""

    return ft.GestureDetector(
        mouse_cursor=ft.MouseCursor.RESIZE_LEFT_RIGHT,
        drag_interval=16,
        on_horizontal_drag_update=lambda e: on_drag(e.local_delta.x if e.local_delta else 0.0),
        on_horizontal_drag_end=lambda _e: on_end() if on_end else None,
        content=ft.Container(width=width, content=ft.VerticalDivider(width=width, thickness=1)),
    )
