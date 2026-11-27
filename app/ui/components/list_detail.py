"""List-detail with a draggable divider (Material's list-detail layout; remembered per page).

The list keeps the width it was dragged to; the detail fills the rest and is
built with its real width (``detail(width)``) so grids and card columns re-flow
while dragging.
"""
from __future__ import annotations

from typing import Callable

import flet as ft

from ...config import app_config
from ..tokens import Space
from .split_handle import SplitHandle

HANDLE = Space.XL  # the divider's grab area


def saved_width(key: str, default: float, low: float, high: float) -> float:
    try:
        value = float(app_config.get_ui_value(key, default) or default)
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


@ft.component
def ListDetail(
    list_pane: ft.Control,
    detail: Callable[[float], ft.Control],
    *,
    total_width: float,
    store_key: str,
    default: float = 248,
    low: float = 200,
    high: float = 420,
    min_detail: float = 360,
):
    width, set_width = ft.use_state(lambda: saved_width(store_key, default, low, high))
    dragged = ft.use_ref(None)
    # Never squeeze the detail below ``min_detail`` however the divider was left.
    shown = max(low, min(width, high, total_width - HANDLE - min_detail))
    dragged.current = shown

    def drag(dx: float) -> None:
        dragged.current = max(low, min(high, total_width - HANDLE - min_detail, dragged.current + dx))
        set_width(dragged.current)

    def end() -> None:
        app_config.set_ui_value(store_key, int(dragged.current))

    return ft.Row(expand=True, spacing=0, vertical_alignment=ft.CrossAxisAlignment.STRETCH, controls=[
        ft.Container(width=shown, content=list_pane),
        SplitHandle(drag, end, width=HANDLE),
        ft.Container(expand=True, content=detail(max(1.0, total_width - shown - HANDLE))),
    ])
