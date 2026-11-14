"""macOS trackpad: a two-finger swipe to the right goes back, as in Safari or Finder.

Qt has no page-swipe gesture for the trackpad; the swipe arrives as a
phased scroll (``ScrollBegin`` → ``ScrollUpdate``… → ``ScrollEnd``, then
momentum).  A mouse wheel has no phase, so it never counts.  A swipe fires
once, when the fingers have travelled far enough mostly sideways, and never
over something that scrolls sideways itself (a wide table, a slider).
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QAbstractScrollArea, QAbstractSlider, QWidget


SWIPE_DISTANCE = 120  # logical pixels of finger travel


def scrolls_sideways(widget: QWidget | None) -> bool:
    """Whether a sideways scroll over ``widget`` already means something there."""

    while widget is not None:
        if isinstance(widget, QAbstractSlider) and widget.orientation() == Qt.Orientation.Horizontal:
            return True
        if isinstance(widget, QAbstractScrollArea):
            bar = widget.horizontalScrollBar()
            if bar is not None and bar.maximum() > bar.minimum():
                return True
        widget = widget.parentWidget()
    return False


class SwipeBackTracker:
    """Feed it the window's wheel events; ``feed`` is True once per back swipe."""

    def __init__(self, distance: int = SWIPE_DISTANCE):
        self._distance = distance
        self._reset(active=False)

    def _reset(self, *, active: bool):
        self._active = active
        self._fired = False
        self._dx = 0.0
        self._dy = 0.0

    def feed(self, event: QWheelEvent, widget: QWidget | None) -> bool:
        phase = event.phase()
        if phase == Qt.ScrollPhase.NoScrollPhase:  # a mouse wheel
            return False
        if phase == Qt.ScrollPhase.ScrollBegin:
            self._reset(active=not scrolls_sideways(widget))
            return False
        if phase in (Qt.ScrollPhase.ScrollEnd, Qt.ScrollPhase.ScrollMomentum):
            self._active = phase == Qt.ScrollPhase.ScrollMomentum and self._active
            return False
        if not self._active or self._fired:
            return False
        delta = event.pixelDelta()
        if delta.isNull():
            delta = event.angleDelta() / 4
        # The finger's own direction: with natural scrolling the deltas follow
        # the fingers (and ``inverted()`` is set); otherwise they are reversed.
        sign = 1 if event.inverted() else -1
        self._dx += sign * delta.x()
        self._dy += abs(delta.y())
        if self._dx >= self._distance and self._dx > 2 * self._dy:
            self._fired = True
            return True
        return False
