"""Pure helpers that decide whether a (resumed) HTTP download can be trusted."""
from __future__ import annotations

import json
import os
import re
import tempfile
from typing import Any, Mapping

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


def classify_416(headers: Mapping[str, str], existing_size: int, expected_total: int = 0) -> str:
    """Judge an HTTP 416 reply to a resume request.

    ``"complete"``  the temp file already has every byte,
    ``"corrupt"``   the server's total differs from what we hold or from
                    ``expected_total`` (the size recorded when the transfer
                    started), or the reply gives no total and nothing recorded
                    vouches for the file.
    """
    _start, _end, total = parse_content_range(headers.get("Content-Range"))
    if expected_total > 0 and existing_size != expected_total:
        return "corrupt"
    if total is None:
        return "complete" if expected_total > 0 else "corrupt"
    return "complete" if total == existing_size else "corrupt"


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


# A partial ``*_temp`` file is resumed only while it still belongs to the same
# source: ``<temp>.resume.json`` records which file / quality it holds, which
# downloader wrote it (``native`` / ``aria2``: their partial files differ) and
# the full size the server announced when the transfer started.
RESUME_META_SUFFIX = ".resume.json"
BACKEND_NATIVE = "native"
BACKEND_ARIA2 = "aria2"


def resume_meta_path(temp_path: str) -> str:
    return f"{temp_path}{RESUME_META_SUFFIX}"


def read_resume_meta(temp_path: str) -> dict[str, Any]:
    """The record next to a partial file; ``{}`` when missing or unreadable."""
    try:
        with open(resume_meta_path(temp_path), "r", encoding="utf-8") as stream:
            data = json.load(stream)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    try:
        total = int(data.get("total", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        return {}
    return {
        "source": str(data.get("source", "") or ""),
        "total": max(0, total),
        "backend": str(data.get("backend", "") or ""),
    }


def write_resume_meta(temp_path: str, source: str, total: int, backend: str = BACKEND_NATIVE) -> None:
    """Atomically record what the partial file holds."""
    target = resume_meta_path(temp_path)
    tmp = ""
    try:
        # A short name beside the target: the partial file's own name may
        # already be close to the file-name length limit.
        fd, tmp = tempfile.mkstemp(prefix=".resume-", suffix=".tmp", dir=os.path.dirname(target) or None)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"source": source, "total": int(total), "backend": backend}, stream)
        os.replace(tmp, target)
    except OSError:
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass


def remove_resume_meta(temp_path: str) -> None:
    try:
        os.remove(resume_meta_path(temp_path))
    except OSError:
        pass


def resume_source_mismatch(meta: Mapping[str, Any], source: str) -> bool:
    """True when the partial file was started from another file or quality."""
    recorded = str(meta.get("source", "") or "")
    return bool(recorded and source and recorded != source)


def partial_file_reusable(temp_path: str, source: str, backend: str) -> bool:
    """Whether an existing partial file may be continued by ``backend``.

    Requires a record written by the same downloader for the same, known
    source.  Partial files without one (older versions) are not trusted.
    """
    meta = read_resume_meta(temp_path)
    if not meta or not source or meta.get("backend") != backend or meta.get("source") != source:
        return False
    has_control_file = os.path.exists(f"{temp_path}.aria2")
    if backend == BACKEND_NATIVE:
        return not has_control_file  # aria2 writes segments with holes; only aria2 can continue it
    return has_control_file  # without aria2's control file the segment map is lost


def resume_total_mismatch(headers: Mapping[str, str], existing_size: int, expected_total: int) -> str:
    """For a 206 reply: explain why its full size differs from the one recorded.

    Uses ``Content-Range``'s total, else ``existing_size + Content-Length``.
    ``""`` when consistent or when nothing was recorded (older partial files).
    """
    if expected_total <= 0:
        return ""
    _start, _end, total = parse_content_range(headers.get("Content-Range"))
    if total is None:
        length = int(headers.get("Content-Length", 0) or 0)
        total = existing_size + length if length > 0 else None
    if total is not None and total != expected_total:
        return f"server file is {total} bytes, partial file was for {expected_total}"
    return ""

