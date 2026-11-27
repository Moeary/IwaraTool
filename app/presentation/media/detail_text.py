"""Text helpers for the detail view: links in descriptions and short dates."""

from __future__ import annotations

import html
import re


_URL_RE = re.compile(r"(https?://[^\s<>\"']+)")


def linkify(text: str) -> str:
    """Escape ``text`` and turn bare URLs into links, keeping line breaks."""

    escaped = html.escape(str(text or ""))
    escaped = _URL_RE.sub(lambda m: f'<a href="{m.group(1)}">{m.group(1)}</a>', escaped)
    return escaped.replace("\r\n", "\n").replace("\n", "<br>")


def link_segments(text: str) -> list[tuple[str, str]]:
    """``text`` split into ``(piece, url)``: ``url`` is "" for plain text (for UIs without HTML)."""

    text = str(text or "").replace("\r\n", "\n")
    segments: list[tuple[str, str]] = []
    last = 0
    for match in _URL_RE.finditer(text):
        if match.start() > last:
            segments.append((text[last:match.start()], ""))
        segments.append((match.group(1), match.group(1)))
        last = match.end()
    if last < len(text):
        segments.append((text[last:], ""))
    return segments


def format_date(value: str) -> str:
    return str(value or "")[:10]
