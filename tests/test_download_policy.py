from __future__ import annotations

import unittest
from datetime import time

from app.core.download_policy import SharedRateLimiter, is_time_in_window, normalize_hhmm


class DownloadPolicyTests(unittest.TestCase):
    def test_normalize_hhmm_falls_back_for_invalid_input(self):
        self.assertEqual(normalize_hhmm("25:30"), "00:00")
        self.assertEqual(normalize_hhmm("09:30"), "09:30")

    def test_daily_and_overnight_windows(self):
        self.assertTrue(is_time_in_window(time(10, 0), "09:00", "17:00"))
        self.assertFalse(is_time_in_window(time(18, 0), "09:00", "17:00"))
        self.assertTrue(is_time_in_window(time(23, 0), "22:00", "06:00"))
        self.assertTrue(is_time_in_window(time(5, 59), "22:00", "06:00"))
        self.assertFalse(is_time_in_window(time(12, 0), "22:00", "06:00"))
        self.assertTrue(is_time_in_window(time(12, 0), "00:00", "00:00"))

    def test_rate_limiter_serializes_shared_bandwidth(self):
        clock = [0.0]

        def monotonic():
            return clock[0]

        def sleep(seconds: float):
            clock[0] += seconds

        limiter = SharedRateLimiter(
            lambda: 100,
            monotonic=monotonic,
            sleeper=sleep,
        )
        self.assertTrue(limiter.throttle(100))
        self.assertEqual(clock[0], 0.0)
        self.assertTrue(limiter.throttle(50))
        self.assertAlmostEqual(clock[0], 1.0)

    def test_rate_limiter_can_cancel_while_waiting(self):
        clock = [0.0]
        waits = [0]

        def sleep(seconds: float):
            clock[0] += seconds
            waits[0] += 1

        limiter = SharedRateLimiter(
            lambda: 10,
            monotonic=lambda: clock[0],
            sleeper=sleep,
        )
        self.assertTrue(limiter.throttle(10))
        self.assertFalse(limiter.throttle(10, cancelled=lambda: waits[0] > 0))


if __name__ == "__main__":
    unittest.main()
