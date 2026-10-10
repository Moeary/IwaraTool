"""Pure helpers that decide whether a (resumed) HTTP download can be trusted."""
from __future__ import annotations

import re
from typing import Mapping

_RANGE_RE = re.compile(r"^\s*bytes\s+(?:(\d+)-(\d+)|\*)/(\d+|\*)\s*$", re.IGNORECASE)


def parse_content_range(value: str | None) -> tuple[int | None, int | None, int | None]:
    """Return ``(start, end, total)``; any part may be ``None`` if unknown."""
    match = _RANGE_RE.match(str(value or ""))
    if not match:
        return None, None, None
    start, end, total = match.groups()
    return (
        int(start) if start is not None else None,
        int(end) if end is not None else None,
        int(total) if total and total != "*" else None,
    )


def resume_offset_mismatch(headers: Mapping[str, str], existing_size: int) -> str:
    """For a 206 reply: explain why the body does not continue our temp file.

    Returns ``""`` when the reply starts exactly at ``existing_size`` (or the
    server did not say, which we accept as before).
    """
    start, _end, _total = parse_content_range(headers.get("Content-Range"))
    if start is not None and start != existing_size:
        return f"server resumed at byte {start}, expected {existing_size}"
    return ""


def classify_416(headers: Mapping[str, str], existing_size: int) -> str:
    """Judge an HTTP 416 reply to a resume request.

    ``"complete"``  the temp file already has every byte (or size unknown),
    ``"corrupt"``   the server's total differs from what we hold.
    """
    _start, _end, total = parse_content_range(headers.get("Content-Range"))
    if total is not None and total != existing_size:
        return "corrupt"
    return "complete"


def incomplete_reason(
    downloaded: int,
    expected_total: int,
    headers: Mapping[str, str],
) -> str:
    """Explain a short/oversized body, or ``""`` when sizes are consistent.

    Skipped when the transfer was content-encoded, because ``Content-Length``
    then counts compressed bytes.
    """
    if expected_total <= 0:
        return ""
    encoding = str(headers.get("Content-Encoding", "") or "").strip().lower()
    if encoding and encoding != "identity":
        return ""
    if downloaded != expected_total:
        return f"received {downloaded} of {expected_total} bytes"
    return ""
