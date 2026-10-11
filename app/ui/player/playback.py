"""Playback speeds, step sizes and time formatting of the video player."""

from __future__ import annotations


SPEEDS = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0)


HOLD_BOOST_MS = 350


HOLD_BOOST_RATE = 2.0


SEEK_STEP_MS = 5000


VOLUME_STEP = 0.05


CONTROLS_HIDE_MS = 2500


def format_time(milliseconds: int) -> str:
    seconds = max(0, int(milliseconds)) // 1000
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def nearest_speed_index(rate: float) -> int:
    return min(range(len(SPEEDS)), key=lambda i: abs(SPEEDS[i] - rate))
