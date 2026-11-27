"""A row of buttons that fills its width: equal widths, as many per line as fit.

Settings cards used to leave their buttons at their natural width on the left,
with most of the line empty.  Here the buttons share the line evenly.  How
many fit depends on the row's own width (a card in a two-column settings pane
is far narrower than the window, so the window's breakpoints do not help): the
row measures itself (``on_size_change``) and splits the buttons into balanced
lines of at most ``per_line`` (four buttons that fit three to a line become two
lines of two, not three and one).
"""
from __future__ import annotations

import math

import flet as ft

from ..tokens import Space

MIN_BUTTON = 150  # an icon and five or six CJK characters without wrapping


def per_line(width: float, count: int, gap: float = Space.SM) -> int:
    """Buttons on one line: as many as fit at ``MIN_BUTTON``, balanced over the lines needed."""
    if count <= 1:
        return 1
    fit = min(count, 2) if width <= 0 else max(1, min(count, int((width + gap) // (MIN_BUTTON + gap))))
    lines = math.ceil(count / fit)
    return math.ceil(count / lines)


def lines_of(items: list, size: int) -> list[list]:
    return [items[start:start + size] for start in range(0, len(items), size)]


@ft.component
def FillRow(controls: list[ft.Control]):
    width, set_width = ft.use_state(0.0)

    def measured(e: ft.LayoutSizeChangeEvent) -> None:
        if abs(e.width - width) >= 1:
            set_width(e.width)

    for control in controls:
        control.expand = True  # built by the caller this render, so still ours to change
    return ft.Container(
        on_size_change=measured,
        content=ft.Column(spacing=Space.SM, controls=[
            ft.Row(spacing=Space.SM, controls=line) for line in lines_of(controls, per_line(width, len(controls)))
        ]),
    )


def fill_row(*controls: ft.Control | None) -> ft.Control:
    return FillRow([c for c in controls if c is not None])
