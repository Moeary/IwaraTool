"""Desktop: a two-finger swipe to the right on the trackpad goes back (``presentation/swipe_back.py``).

Flutter delivers a trackpad pan to the horizontal-drag handlers with the
device kind ``trackpad``; mouse drags and touches are ignored (phones have the
system back gesture).  Anything inside that scrolls sideways itself (the home
shelves, sliders, text fields) wins the gesture, as in the classic window.
"""
from __future__ import annotations

from collections.abc import Callable

import flet as ft

from ...presentation.swipe_back import SwipeDistance

DRAG_INTERVAL_MS = 30  # updates sent to Python at most this often while the fingers move


@ft.component
def SwipeBack(content: ft.Control, on_back: Callable[[], None]):
    travel = ft.use_ref(None)
    if travel.current is None:
        travel.current = {"distance": SwipeDistance(), "trackpad": False}
    state = travel.current

    def start(e: ft.DragStartEvent) -> None:
        state["trackpad"] = e.kind == ft.PointerDeviceType.TRACKPAD
        state["distance"].begin()

    def update(e: ft.DragUpdateEvent) -> None:
        if not state["trackpad"]:
            return
        delta = e.local_delta
        dx = delta.x if delta is not None else (e.primary_delta or 0.0)
        dy = delta.y if delta is not None else 0.0
        if state["distance"].move(dx, dy):
            on_back()

    return ft.GestureDetector(
        expand=True,
        drag_interval=DRAG_INTERVAL_MS,
        on_horizontal_drag_start=start,
        on_horizontal_drag_update=update,
        content=content,
    )
