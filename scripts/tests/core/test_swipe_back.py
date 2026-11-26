"""The shared back-swipe distance rule (presentation/swipe_back.py)."""
from __future__ import annotations

import unittest

from app.presentation.swipe_back import SWIPE_DISTANCE, SwipeDistance


class SwipeDistanceTests(unittest.TestCase):
    def test_fires_once_far_enough_to_the_right(self):
        travel = SwipeDistance()
        fired = [travel.move(10, 0) for _ in range(30)]
        self.assertEqual(fired.count(True), 1)
        self.assertTrue(fired[SWIPE_DISTANCE // 10 - 1])

    def test_left_short_or_vertical_does_not_fire(self):
        for steps, dx, dy in ((30, -10, 0), (5, 10, 0), (30, 10, 8)):
            travel = SwipeDistance()
            self.assertFalse(any(travel.move(dx, dy) for _ in range(steps)), (steps, dx, dy))

    def test_begin_starts_a_new_gesture(self):
        travel = SwipeDistance(distance=20)
        self.assertTrue(travel.move(25, 0))
        travel.begin()
        self.assertTrue(travel.move(25, 0))


if __name__ == "__main__":
    unittest.main()
