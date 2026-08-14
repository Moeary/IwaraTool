"""Pure scheduling helpers and a process-wide shared rate limiter."""
from __future__ import annotations

import threading
import time
from datetime import datetime, time as clock_time
from typing import Callable


def normalize_hhmm(value: str, default: str = "00:00") -> str:
    """Normalize an HH:MM value; invalid input falls back to *default*."""
    try:
        parsed = datetime.strptime(str(value or "").strip(), "%H:%M")
    except ValueError:
        try:
            parsed = datetime.strptime(default, "%H:%M")
        except ValueError:
            parsed = datetime.strptime("00:00", "%H:%M")
    return parsed.strftime("%H:%M")


def is_time_in_window(now: clock_time, start: str, end: str) -> bool:
    """Return whether *now* is in a daily window, including overnight spans.

    Equal start/end values intentionally mean all day instead of an empty
    window, which keeps a newly enabled schedule from deadlocking the queue.
    """
    start_value = datetime.strptime(normalize_hhmm(start), "%H:%M").time()
    end_value = datetime.strptime(normalize_hhmm(end), "%H:%M").time()
    current = now.replace(second=0, microsecond=0)
    if start_value == end_value:
        return True
    if start_value < end_value:
        return start_value <= current < end_value
    return current >= start_value or current < end_value


class SharedRateLimiter:
    """Leaky-bucket limiter shared by every built-in download worker."""

    def __init__(
        self,
        rate_provider: Callable[[], int],
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        self._rate_provider = rate_provider
        self._monotonic = monotonic
        self._sleeper = sleeper
        self._lock = threading.Lock()
        self._next_available = 0.0
        self._last_rate = 0

    def throttle(
        self,
        byte_count: int,
        *,
        cancelled: Callable[[], bool] | None = None,
        on_wait: Callable[[], None] | None = None,
    ) -> bool:
        amount = max(0, int(byte_count))
        if amount <= 0:
            return True
        try:
            rate = max(0, int(self._rate_provider()))
        except (TypeError, ValueError):
            rate = 0
        if rate <= 0:
            with self._lock:
                self._last_rate = 0
                self._next_available = 0.0
            return True

        with self._lock:
            now = self._monotonic()
            if rate != self._last_rate:
                self._next_available = now
                self._last_rate = rate
            scheduled_at = max(now, self._next_available)
            self._next_available = scheduled_at + (amount / rate)

        while True:
            if cancelled and cancelled():
                return False
            remaining = scheduled_at - self._monotonic()
            if remaining <= 0:
                return True
            if on_wait:
                on_wait()
            self._sleeper(min(0.1, remaining))
