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


def format_date(value: str) -> str:
    return str(value or "")[:10]
