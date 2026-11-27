"""A two-finger trackpad swipe to the right goes back (as in Safari or Finder).

Each interface feeds the finger travel of one gesture (``begin`` then
``move``); ``move`` is True once, when the fingers have travelled far enough
and mostly sideways.  How the gesture arrives is the interface's business:
phased wheel events in Qt (``ui/window/swipe_back.py``), a trackpad drag in
Flet (``ui/components/swipe_back.py``).  No Qt, no Flet here.
"""
from __future__ import annotations

SWIPE_DISTANCE = 120  # logical pixels of finger travel


class SwipeDistance:
    def __init__(self, distance: float = SWIPE_DISTANCE):
        self.distance = distance
        self.begin()

    def begin(self) -> None:
        self.fired = False
        self.dx = 0.0
        self.dy = 0.0

    def move(self, dx: float, dy: float) -> bool:
        """Add finger travel (``dx`` > 0 is to the right); True when this gesture is a back swipe."""
        if self.fired:
            return False
        self.dx += dx
        self.dy += abs(dy)
        if self.dx >= self.distance and self.dx > 2 * self.dy:
            self.fired = True
            return True
        return False
