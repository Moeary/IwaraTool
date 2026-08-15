"""Pure helpers used by the downloaded-file repair workflow."""
from __future__ import annotations

import hashlib
import os
import re
import unicodedata
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any


VIDEO_EXTENSIONS = frozenset(
    {
        ".avi",
        ".flv",
        ".m4v",
        ".mkv",
        ".mov",
        ".mp4",
        ".mpeg",
        ".mpg",
        ".ts",
        ".webm",
        ".wmv",
    }
)

_VIDEO_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.|api\.)?iwara\.tv/video/([A-Za-z0-9][A-Za-z0-9_-]*)",
    re.IGNORECASE,
)
_VIDEO_ID_LABEL_RE = re.compile(
    r"(?:iwara(?:[ _-]*(?:video|id))?|video[ _-]*id)[\s:=#_\-\[\(]+"
    r"([A-Za-z0-9]{8,})",
    re.IGNORECASE,
)
_DATE_PREFIX_RE = re.compile(
    r"^[\s._()\[\]-]*(?:\d{4}[-_.]\d{1,2}[-_.]\d{1,2}|\d{8})"
    r"[\s._()\[\]-]*",
    re.IGNORECASE,
)
_QUALITY_SUFFIX_RE = re.compile(
    r"(?:[\s._-]+)(?:source|原画|original|1080p|720p|540p|360p)$",
    re.IGNORECASE,
)
_INVALID_FILENAME_CHARS_RE = re.compile(r'[\x00-\x1f<>:"/\\|?*]')
_WINDOWS_RESERVED_FILENAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{number}" for number in range(1, 10)),
        *(f"LPT{number}" for number in range(1, 10)),
    }
)


def extract_iwara_video_id(value: str) -> str:
    """Extract an Iwara video ID from a URL or an explicitly labelled name."""

    text = str(value or "").strip()
    if not text:
        return ""
    match = _VIDEO_URL_RE.search(text)
    if match:
        return match.group(1).strip()
    match = _VIDEO_ID_LABEL_RE.search(text)
    return match.group(1).strip() if match else ""


def guess_filename_video_id(value: str) -> str:
    """Return a conservative trailing ID candidate from a download filename.

    The candidate is only a lookup hint. Callers must still validate it with
    the Iwara API before changing anything, so ordinary title words cannot
    silently become video IDs.
    """

    stem = os.path.splitext(os.path.basename(str(value or "")))[0]
    stem = _DATE_PREFIX_RE.sub("", stem)
    stem = _QUALITY_SUFFIX_RE.sub("", stem)
    tokens = [token for token in re.split(r"[\s._-]+", stem) if token]
    if not tokens:
        return ""
    candidate = tokens[-1]
    if re.fullmatch(r"[A-Za-z0-9]{8,}", candidate):
        return candidate
    return ""


def scan_video_files(folder: str) -> list[str]:
    """Return non-empty video files below ``folder`` in stable path order."""

    root = os.path.abspath(os.path.expanduser(str(folder or "").strip()))
    if not os.path.isdir(root):
        return []

    result: list[str] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for filename in filenames:
            lower_name = filename.casefold()
            if lower_name.endswith("_temp") or lower_name.endswith(".aria2"):
                continue
            if os.path.splitext(lower_name)[1] not in VIDEO_EXTENSIONS:
                continue
            path = os.path.join(dirpath, filename)
            try:
                if os.path.isfile(path) and os.path.getsize(path) > 0:
                    result.append(path)
            except OSError:
                continue
    return sorted(result, key=lambda value: value.casefold())


def normalize_match_text(value: Any) -> str:
    """Normalize titles/authors for conservative local matching."""

    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return " ".join(re.findall(r"[\w]+", text, flags=re.UNICODE))


def filename_search_text(path: str) -> str:
    """Turn a downloaded filename into a title-like search string."""

    stem = os.path.splitext(os.path.basename(str(path or "")))[0]
    stem = _DATE_PREFIX_RE.sub("", stem)
    stem = _QUALITY_SUFFIX_RE.sub("", stem)
    stem = re.sub(r"(?:^|[\s._-])(?:iwara|video)[\s._-]*id[\s._:#-]*[A-Za-z0-9]{8,}", " ", stem, flags=re.IGNORECASE)
    stem = re.sub(r"[_]+", " ", stem)
    return stem.strip(" .-_[]()") or os.path.splitext(os.path.basename(str(path or "")))[0]


def candidate_match_score(path: str, candidate: dict[str, Any], *, author_hint: str = "") -> float:
    """Score a local/API candidate against a filename.

    Exact title matches are deliberately much stronger than fuzzy matches. An
    author directory match is a useful tie breaker for duplicate titles.
    """

    filename_title = normalize_match_text(filename_search_text(path))
    candidate_title = normalize_match_text(candidate.get("title", ""))
    if not filename_title or not candidate_title:
        return 0.0
    ratio = SequenceMatcher(None, filename_title, candidate_title).ratio()
    score = ratio
    if filename_title == candidate_title:
        score += 1.0

    author_hint = normalize_match_text(author_hint)
    candidate_author = normalize_match_text(
        candidate.get("author")
        or candidate.get("username")
        or candidate.get("source_key")
        or ""
    )
    if author_hint and candidate_author:
        if author_hint == candidate_author:
            score += 0.35
        elif author_hint in candidate_author or candidate_author in author_hint:
            score += 0.15
    return score


def format_repair_filename(
    template: str,
    metadata: dict[str, Any],
    original_path: str,
) -> str:
    """Expand a naming template into a safe relative repair path.

    Directory segments are intentionally preserved. The caller decides which
    output root to use, so ``{author}/...`` can organize repaired files below
    the selected folder without allowing an absolute path or ``..`` segment to
    escape it. The source extension is retained for webm/mkv and other local
    video formats.
    """

    published_at = str(metadata.get("published_at", "") or "").strip()
    date_text = published_at[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", published_at) else ""
    if not date_text:
        try:
            date_text = datetime.fromtimestamp(os.path.getmtime(original_path)).strftime("%Y-%m-%d")
        except OSError:
            date_text = datetime.now().strftime("%Y-%m-%d")
    try:
        year, month, day = date_text.split("-")
    except ValueError:
        year, month, day = date_text, "", ""

    author = str(metadata.get("author", "") or metadata.get("username", "") or "unknown").strip() or "unknown"
    values = {
        "{YYYY-MM-DD}": date_text,
        "{YYYY}": year,
        "{MM}": month,
        "{DD}": day,
        "{date}": date_text,
        "{title}": str(metadata.get("title", "") or metadata.get("video_id", "") or "video"),
        "{id}": str(metadata.get("video_id", "") or metadata.get("id", "")),
        "{username}": author,
        "{author}": author,
        "{quality}": str(metadata.get("quality", "") or "unknown"),
        "{likes}": str(metadata.get("likes", 0) or 0),
        "{views}": str(metadata.get("views", 0) or 0),
        "{comments}": str(metadata.get("comments", 0) or 0),
        "{duration}": str(metadata.get("duration", 0) or 0),
        "{slug}": str(metadata.get("slug", "") or ""),
        "{rating}": str(metadata.get("rating", "") or ""),
    }
    raw_template = str(template or "").strip().replace("\\", "/")
    raw_template = raw_template or "{YYYY-MM-DD}_{title}_{id}"
    raw_segments = [
        segment.strip()
        for segment in raw_template.lstrip("/").split("/")
        if segment.strip() not in {"", ".", ".."}
    ]
    if not raw_segments:
        raw_segments = ["{YYYY-MM-DD}_{title}_{id}"]

    expanded_segments = []
    for segment in raw_segments:
        expanded = segment
        for token, value in values.items():
            expanded = expanded.replace(token, value)
        expanded_segments.append(expanded)

    source_ext = os.path.splitext(str(original_path or ""))[1].lower() or ".mp4"
    safe_directories = [
        safe_segment
        for segment in expanded_segments[:-1]
        for safe_segment in [_safe_filename(segment)]
        if safe_segment
    ]
    name_stem, _template_ext = os.path.splitext(expanded_segments[-1])
    name = name_stem or str(metadata.get("video_id", "") or "video")
    name = _safe_filename(name)
    if not name:
        name = str(metadata.get("video_id", "") or "video")
    filename = f"{name}{source_ext}"
    return os.path.join(*safe_directories, filename) if safe_directories else filename


def _safe_filename(value: str) -> str:
    cleaned = _INVALID_FILENAME_CHARS_RE.sub("-", str(value or "")).strip(" .")
    if not cleaned or cleaned in {".", ".."}:
        return ""
    if cleaned.split(".", 1)[0].rstrip(" ").upper() in _WINDOWS_RESERVED_FILENAMES:
        cleaned = f"_{cleaned}"
    if len(cleaned) <= 230:
        return cleaned
    stem, extension = os.path.splitext(cleaned)
    digest = hashlib.sha1(cleaned.encode("utf-8")).hexdigest()[:10]
    keep = max(1, 230 - len(extension) - len(digest) - 2)
    return f"{stem[:keep]}-{digest}{extension}"
