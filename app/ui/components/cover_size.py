"""The cover-size slider every grid follows (desktop; ``presentation/media/card_size.py``)."""
from __future__ import annotations

import flet as ft

from ...presentation.media import card_size
from ..icons import AppIcon
from ..stores.app_state import use_shell
from ..tokens import Size, Space

SLIDER_WIDTH = 120
STEP = card_size.CARD_WIDTH_STEP // 2  # snapping keeps a drag to a few dozen re-layouts; the default sits on a notch


@ft.component
def CoverSizeSlider():
    shell = use_shell()
    if not shell.caps.desktop:
        return ft.Container(width=0)
    low, high = card_size.CARD_WIDTH_MIN, card_size.CARD_WIDTH_MAX
    return ft.Row(
        tight=True,
        spacing=Space.XXS,
        tooltip=card_size.cover_size_label(),
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[
            ft.Icon(AppIcon.ZOOM_OUT, size=Size.ICON_SM),
            ft.Slider(
                width=SLIDER_WIDTH,
                padding=ft.Padding.symmetric(horizontal=Space.SM),
                min=low,
                max=high,
                divisions=(high - low) // STEP,
                value=shell.state.cover_size,
                on_change=lambda e: shell.set_cover_size(e.control.value, save=False),
                on_change_end=lambda e: shell.set_cover_size(e.control.value),
            ),
            ft.Icon(AppIcon.ZOOM_IN, size=Size.ICON_SM),
        ],
    )
