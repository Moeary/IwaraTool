"""Pure helpers used by the downloaded-file repair workflow."""
from __future__ import annotations

import hashlib
import os
import re
import unicodedata
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import urlparse

from .. import site as sites


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
    r"(?:https?://)?(?<![A-Za-z0-9.-])(?:www\.|api\.)?iwara\.(?:tv|ai)/video/([A-Za-z0-9][A-Za-z0-9_-]*)",
    re.IGNORECASE,
)
# An explicit "id" label: ``iwara-id=…``, ``Iwara ID …``, ``video_id: …``,
# ``id=…``. A bare ``Iwara - `` prefix is *not* a label: browser downloads are
# named ``Iwara - <title> [<id>]`` and the title's first word is no ID. A bare
# ``id`` needs ``:``/``=``/``#`` so a title word such as "My ID Challenge"
# is not read as one.
_VIDEO_ID_LABEL_RE = re.compile(
    r"(?:(?:iwara|video)[ _-]*id[\s:=#_\-]+|(?<![A-Za-z0-9])id\s*[:=#]\s*)[\[\(]?"
    r"([A-Za-z0-9]{8,})",
    re.IGNORECASE,
)
_MANUAL_URL_PATH_RE = re.compile(r"^/video/([A-Za-z0-9][A-Za-z0-9_-]*)(?:/|$)")
# ``[id]`` / ``(id)`` / ``{id}`` / ``【id】`` as written by yt-dlp style tools.
_BRACKETED_TOKEN_RE = re.compile(
    r"[\[\(\{【（]\s*([A-Za-z0-9]{8,24})\s*[\]\)\}】）]"
)
_ID_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z0-9]{8,24})(?![A-Za-z0-9])")
# A bare ID typed or pasted by the user (Iwara IDs may contain ``_``/``-``).
_MANUAL_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{3,63}$")
_IWARA_PREFIX_RE = re.compile(r"^\s*iwara\s*[-_:：]\s*", re.IGNORECASE)
_BRACKETED_QUALITY_RE = re.compile(
    r"[\[\(\{【（]\s*(?:source|原画|original|\d{3,4}p|4k)\s*[\]\)\}】）]",
    re.IGNORECASE,
)
# How many unlabelled filename tokens are checked online before searching.
MAX_FILENAME_ID_GUESSES = 3
_DATE_PREFIX_RE = re.compile(
    r"^[\s._()\[\]-]*(?:\d{4}[-_.]\d{1,2}[-_.]\d{1,2}|\d{8})"
    r"[\s._()\[\]-]*",
    re.IGNORECASE,
)
_QUALITY_SUFFIX_RE = re.compile(
    r"(?:[\s._-]+)[\[\(]?(?:source|原画|original|1080p|720p|540p|360p)[\]\)]?$",
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


def parse_manual_video_id(value: str) -> str:
    """The video ID in a user-entered iwara.tv / iwara.ai video link or bare ID, or ``""``."""

    text = str(value or "").strip()
    if not text:
        return ""
    if _MANUAL_VIDEO_ID_RE.fullmatch(text):
        return text
    if not sites.site_of_url(text):
        return ""
    if "://" not in text:
        text = "https://" + text.lstrip("/")
    try:
        match = _MANUAL_URL_PATH_RE.match(urlparse(text).path)
    except ValueError:
        return ""
    return match.group(1) if match else ""


def filename_stem(value: str) -> str:
    """A file's name without a video extension; any other "extension" is title text.

    Callers may pass a full path or an already extension-less stem: a title
    such as ``Part 1.5 [id]`` keeps everything after its dot.
    """

    name = os.path.basename(str(value or ""))
    stem, extension = os.path.splitext(name)
    return stem if extension.lower() in VIDEO_EXTENSIONS else name


def looks_like_video_id(token: str) -> bool:
    """Whether an unlabelled token reads like a generated ID rather than a word."""

    has_digit = any(char.isdigit() for char in token)
    has_alpha = any(char.isalpha() for char in token)
    if has_digit and has_alpha:
        return True
    # Generated IDs mix case at random; ``TimeStop`` or ``SEXDance`` only
    # change case at word starts, so a lone case mix needs a longer token.
    if has_alpha and not has_digit and len(token) >= 12:
        rest = token[1:]
        return any(char.isupper() for char in rest) and any(char.islower() for char in rest)
    return False


def filename_video_id_guesses(value: str) -> list[str]:
    """Unlabelled ID candidates in a filename, most likely first.

    These are lookup hints only: callers validate each with the Iwara API and
    fall back to a title search, so a title word can never silently become an
    ID. Bracketed tokens (``Title [AbC123xyz] [Source]``) rank first, then the
    trailing token of the app's own ``…_{id}`` names, then other ID-like
    tokens nearest the end of the name.
    """

    stem = _DATE_PREFIX_RE.sub("", filename_stem(value))
    ranked: list[tuple[int, int, str]] = []

    for match in _BRACKETED_TOKEN_RE.finditer(stem):
        token = match.group(1)
        if looks_like_video_id(token):
            ranked.append((0, -match.start(), token))

    trailing = _QUALITY_SUFFIX_RE.sub("", _BRACKETED_QUALITY_RE.sub("", stem).rstrip(" ._-"))
    trailing_match = re.search(r"(?:^|([\s._-]))([A-Za-z0-9]{8,})$", trailing)
    if trailing_match:
        token = trailing_match.group(2)
        if looks_like_video_id(token):
            ranked.append((1, 0, token))
        elif trailing_match.group(1) == "_":
            # The app's own ``…_{id}`` names: an ID need not look random, so
            # an ``_``-separated word is still tried, last.
            ranked.append((3, 0, token))

    for match in _ID_TOKEN_RE.finditer(stem):
        token = match.group(1)
        if looks_like_video_id(token) and len(token) >= 10:
            ranked.append((2, -match.start(), token))

    result: list[str] = []
    seen: set[str] = set()
    for _rank, _position, token in sorted(ranked):
        key = token.casefold()
        if key not in seen:
            seen.add(key)
            result.append(token)
    return result[:MAX_FILENAME_ID_GUESSES]


def guess_filename_video_id(value: str) -> str:
    """The most likely unlabelled ID candidate in a filename, or ``""``."""

    guesses = filename_video_id_guesses(value)
    return guesses[0] if guesses else ""


def scan_video_files(folder: str) -> list[str]:
    """Return non-empty video files below ``folder`` in stable path order.

    ``os.scandir`` entries carry the type and size from the directory listing
    on Windows, so large (or network) folders need no extra stat per file.
    """

    root = os.path.abspath(os.path.expanduser(str(folder or "").strip()))
    if not os.path.isdir(root):
        return []

    result: list[str] = []
    pending = [root]
    while pending:
        try:
            with os.scandir(pending.pop()) as entries:
                for entry in entries:
                    path = _video_entry_path(entry, pending)
                    if path:
                        result.append(path)
        except OSError:
            continue
    return sorted(result, key=lambda value: value.casefold())


def _video_entry_path(entry: os.DirEntry, pending: list[str]) -> str:
    """``entry``'s path if it is a scannable video; queues sub-directories in ``pending``."""

    try:
        if entry.is_dir():
            # Like os.walk(followlinks=False): list linked folders, never descend.
            if not entry.is_symlink():
                pending.append(entry.path)
            return ""
        lower_name = entry.name.casefold()
        if lower_name.endswith("_temp") or lower_name.endswith(".aria2"):
            return ""
        if os.path.splitext(lower_name)[1] not in VIDEO_EXTENSIONS:
            return ""
        if entry.is_file() and entry.stat().st_size > 0:
            return entry.path
    except OSError:
        pass
    return ""


def normalize_match_text(value: Any) -> str:
    """Normalize titles/authors for conservative local matching."""

    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return " ".join(re.findall(r"[\w]+", text, flags=re.UNICODE))


def filename_search_text(path: str, *, strip_ids: bool = True) -> str:
    """Turn a downloaded filename into a title-like search string.

    ``strip_ids`` also drops bracketed tokens that look like IDs. They may be
    title text (``Dance [2024Edition]``), so local matching tries the name
    with them as well.
    """

    original = filename_stem(path)
    stem = _DATE_PREFIX_RE.sub("", original)
    stem = _VIDEO_ID_LABEL_RE.sub(" ", stem)
    stem = _IWARA_PREFIX_RE.sub("", stem)
    stem = _BRACKETED_QUALITY_RE.sub(" ", stem)
    stem = _QUALITY_SUFFIX_RE.sub("", stem.rstrip(" ._-"))
    if strip_ids:
        guesses = {token.casefold() for token in filename_video_id_guesses(original)}
        stem = _BRACKETED_TOKEN_RE.sub(
            lambda match: " " if match.group(1).casefold() in guesses else match.group(0),
            stem,
        )
    stem = re.sub(r"[_]+", " ", stem)
    stem = re.sub(r"\s{2,}", " ", stem)
    return stem.strip(" .-_") or original


def author_match_bonus(author_hint: str, candidate_author: str) -> float:
    """Score bonus for a folder name that matches a candidate's (normalized) author."""

    if not author_hint or not candidate_author:
        return 0.0
    if author_hint == candidate_author:
        return 0.35
    if author_hint in candidate_author or candidate_author in author_hint:
        return 0.15
    return 0.0


def candidate_author_text(candidate: dict[str, Any]) -> str:
    """The normalized author a candidate is matched against."""

    return normalize_match_text(
        candidate.get("author")
        or candidate.get("username")
        or candidate.get("source_key")
        or ""
    )


def candidate_match_score(
    path: str, candidate: dict[str, Any], *, author_hint: str = "", title: str | None = None,
) -> float:
    """Score a local/API candidate against a filename (or ``title`` taken from it).

    Exact title matches are deliberately much stronger than fuzzy matches. An
    author directory match is a useful tie breaker for duplicate titles.
    """

    filename_title = normalize_match_text(filename_search_text(path) if title is None else title)
    candidate_title = normalize_match_text(candidate.get("title", ""))
    if not filename_title or not candidate_title:
        return 0.0
    ratio = SequenceMatcher(None, filename_title, candidate_title).ratio()
    score = ratio
    if filename_title == candidate_title:
        score += 1.0
    return score + author_match_bonus(normalize_match_text(author_hint), candidate_author_text(candidate))


# Candidates scoring below this never change ``pick_repair_candidate``'s
# decision: below it they cannot be the pick, and as runner-up they cannot
# narrow a pick's lead (>= 1.15 or >= 1.95) under the required gap.
CANDIDATE_SCORE_FLOOR = 0.9


def pick_repair_candidate(
    ranked: list[tuple[float, dict[str, Any]]],
) -> tuple[dict[str, Any] | None, bool]:
    """``(clear winner or None, ambiguous)`` among ``(score, candidate)`` pairs."""

    ranked = sorted(ranked, key=lambda pair: pair[0], reverse=True)
    if not ranked:
        return None, False

    top_score, top = ranked[0]
    second_score = ranked[1][0] if len(ranked) > 1 else 0.0
    exact = top_score >= 1.95
    if exact and (len(ranked) == 1 or top_score - second_score >= 0.12):
        return top, False
    if top_score >= 1.15 and (len(ranked) == 1 or top_score - second_score >= 0.2):
        return top, False
    return None, top_score >= CANDIDATE_SCORE_FLOOR


def _repair_template_values(metadata: dict[str, Any], original_path: str) -> dict[str, str]:
    """Placeholder -> value for a repair naming template."""

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

    user = metadata.get("user") if isinstance(metadata.get("user"), dict) else {}
    author = str(
        metadata.get("author")
        or user.get("username")
        or user.get("id")
        or metadata.get("username")
        or "unknown"
    ).strip() or "unknown"
    username = str(
        metadata.get("username")
        or user.get("name")
        or user.get("displayName")
        or author
    ).strip() or author
    values = {
        "{YYYY-MM-DD}": date_text,
        "{YYYY}": year,
        "{MM}": month,
        "{DD}": day,
        "{date}": date_text,
        "{title}": str(metadata.get("title", "") or metadata.get("video_id", "") or "video"),
        "{id}": str(metadata.get("video_id", "") or metadata.get("id", "")),
        "{username}": username,
        "{author}": author,
        "{quality}": str(metadata.get("quality", "") or "unknown"),
        "{likes}": str(metadata.get("likes", 0) or 0),
        "{views}": str(metadata.get("views", 0) or 0),
        "{comments}": str(metadata.get("comments", 0) or 0),
        "{duration}": str(metadata.get("duration", 0) or 0),
        "{slug}": str(metadata.get("slug", "") or ""),
        "{rating}": str(metadata.get("rating", "") or ""),
    }
    return values


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

    values = _repair_template_values(metadata, original_path)
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


def repair_target_path(
    template: str,
    metadata: dict[str, Any],
    original_path: str,
    *,
    output_root: str = "",
    move_to_output: bool = False,
) -> tuple[str, str]:
    """Return the relative name and absolute target used by every repair phase.

    In-place repair applies only the final filename segment of a template.
    Organizing applies its directory segments below the chosen output root.
    """

    source = os.path.abspath(os.path.expanduser(str(original_path or "")))
    relative_name = format_repair_filename(template, metadata, source)
    if move_to_output:
        root = os.path.abspath(os.path.expanduser(str(output_root or os.path.dirname(source))))
    else:
        root = os.path.dirname(source)
        relative_name = os.path.basename(relative_name)
    return relative_name, os.path.join(root, relative_name)


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
