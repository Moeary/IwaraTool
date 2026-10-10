"""Human-readable transfer speeds and sizes."""

from __future__ import annotations


def _fmt_speed(bps: float) -> str:
    if bps >= 1024**2:
        return f"{bps / 1024**2:.1f} MB/s"
    if bps >= 1024:
        return f"{bps / 1024:.1f} KB/s"
    return f"{bps:.0f} B/s"


def format_speed(bps: float) -> str:
    """Public name for the speed text shown in the task list."""

    return _fmt_speed(max(0.0, float(bps or 0)))


def parse_speed(text: str) -> float:
    """Bytes per second for text produced by ``_fmt_speed`` (``"1.5 MB/s"``); 0 if blank."""

    parts = str(text or "").strip().split()
    if len(parts) != 2:
        return 0.0
    unit = {"B/S": 1.0, "KB/S": 1024.0, "MB/S": 1024.0**2, "GB/S": 1024.0**3}.get(parts[1].upper())
    if unit is None:
        return 0.0
    try:
        return max(0.0, float(parts[0])) * unit
    except ValueError:
        return 0.0


def _fmt_bytes(n: int) -> str:
    if n >= 1024**3:
        return f"{n / 1024**3:.1f} GB"
    if n >= 1024**2:
        return f"{n / 1024**2:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n} B"
