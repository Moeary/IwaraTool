"""Cards in one column, or two when the pane is wide enough (no empty half on wide windows)."""
from __future__ import annotations

import flet as ft

from ..tokens import Space

MIN_CARD = 400  # a settings / tool card stays readable down to this width


def column_count(width: float, *, min_card: float = MIN_CARD, gap: float = Space.MD) -> int:
    return 2 if width >= 2 * min_card + gap else 1


def card_columns(cards: list[ft.Control | None], width: float, *, scroll: bool = True,
                 min_card: float = MIN_CARD) -> ft.Control:
    """``cards`` in order; with two columns, each goes to the shorter side by count (left first)."""
    cards = [c for c in cards if c is not None]
    padding = ft.Padding.only(bottom=Space.XL)
    if column_count(width, min_card=min_card) == 1:
        if not scroll:
            return ft.Column(spacing=Space.MD, horizontal_alignment=ft.CrossAxisAlignment.STRETCH, controls=cards)
        return ft.ListView(expand=True, spacing=Space.MD, padding=padding, controls=cards)
    left, right = cards[0::2], cards[1::2]
    row = ft.Row(spacing=Space.MD, vertical_alignment=ft.CrossAxisAlignment.START, controls=[
        ft.Column(expand=True, spacing=Space.MD, horizontal_alignment=ft.CrossAxisAlignment.STRETCH, controls=left),
        ft.Column(expand=True, spacing=Space.MD, horizontal_alignment=ft.CrossAxisAlignment.STRETCH, controls=right),
    ])
    if not scroll:
        return row
    return ft.ListView(expand=True, padding=padding, controls=[row])
