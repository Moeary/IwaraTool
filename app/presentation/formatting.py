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


def format_age(seconds: float) -> str:
    """"just now" / "5 min ago" / "3 h ago" / "2 d ago" in the UI language."""

    from ..i18n import tr

    seconds = max(0.0, float(seconds))
    if seconds < 60:
        return tr("just now", "刚刚", "たった今")
    minutes = int(seconds // 60)
    if minutes < 60:
        return tr(f"{minutes} min ago", f"{minutes} 分钟前", f"{minutes}分前")
    hours = minutes // 60
    if hours < 48:
        return tr(f"{hours} h ago", f"{hours} 小时前", f"{hours}時間前")
    days = hours // 24
    return tr(f"{days} d ago", f"{days} 天前", f"{days}日前")
