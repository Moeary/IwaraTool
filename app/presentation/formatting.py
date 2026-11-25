"""Short text forms of counts, durations and long strings for cards and tables."""

from __future__ import annotations


def _format_duration(seconds: float) -> str:
    total = max(0, int(seconds or 0))
    minutes, remainder = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{remainder:02d}"
    return f"{minutes}:{remainder:02d}"


def _format_count(value: int) -> str:
    value = int(value or 0)
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return str(value)


def _short_text(value: str, length: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= length else f"{text[: max(1, length - 1)]}…"
