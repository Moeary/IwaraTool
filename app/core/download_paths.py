"""Portable output-path construction for downloaded media."""
from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime

from ..config import DEFAULT_FILENAME_TEMPLATE, app_config
from .task_metadata import extract_date_text


WINDOWS_SAFE_PATH_LIMIT = 240
WINDOWS_MIN_PATH_SEGMENT_LENGTH = 32
WINDOWS_RESERVED_FILENAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{number}" for number in range(1, 10)),
        *(f"LPT{number}" for number in range(1, 10)),
    }
)
FILENAME_TEMPLATE_TOKENS = frozenset(
    {
        "YYYY-MM-DD",
        "YYYY",
        "MM",
        "DD",
        "date",
        "title",
        "id",
        "username",
        "author",
        "quality",
        "views",
        "likes",
        "comments",
        "duration",
        "slug",
        "rating",
    }
)
_TEMPLATE_TOKEN_RE = re.compile(r"\{([^{}]+)\}")


def validate_filename_template(template: str) -> tuple[bool, str]:
    """Validate a portable relative output template before it is saved."""

    raw = str(template or "").strip().replace("\\", "/")
    if not raw:
        return False, "Template cannot be empty"
    if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        return False, "Template must be a relative path"
    if any(segment.strip() in {".", ".."} for segment in raw.split("/")):
        return False, "Template cannot contain . or .. path segments"

    tokens = _TEMPLATE_TOKEN_RE.findall(raw)
    unknown = sorted({token for token in tokens if token not in FILENAME_TEMPLATE_TOKENS})
    if unknown:
        return False, f"Unknown placeholder: {{{unknown[0]}}}"
    without_tokens = _TEMPLATE_TOKEN_RE.sub("", raw)
    if "{" in without_tokens or "}" in without_tokens:
        return False, "Template contains an unmatched brace"
    if any(char in without_tokens for char in '\x00\r\n<>:"|?*'):
        return False, "Template contains invalid Windows filename characters"
    if not raw.rstrip("/").split("/")[-1].strip():
        return False, "Template must include a filename"
    return True, ""


class DownloadPathMixin:
    """Build and sanitize download paths without coupling them to scheduling."""

    def _build_output_relative_path(
        self,
        *,
        title: str,
        video_id: str,
        author: str,
        published_at: str,
        quality: str,
        likes: int,
        views: int,
        comments: int,
        duration: int,
        slug: str,
        rating: str,
        filename_template: str | None = None,
        username: str = "",
    ) -> str:
        raw_template = (
            app_config.filename_template if filename_template is None else filename_template
        ) or ""
        raw_template = raw_template.strip() or DEFAULT_FILENAME_TEMPLATE
        template = raw_template.replace("\\", "/")

        date_text = extract_date_text(published_at) or datetime.now().strftime("%Y-%m-%d")
        year, month, day = date_text.split("-")
        author_value = (author or username or "unknown").strip() or "unknown"
        username_value = (username or author_value or "unknown").strip() or "unknown"

        def safe(value) -> str:
            return str(value).replace("/", "-").replace("\\", "-")

        mapping = {
            "{YYYY-MM-DD}": safe(date_text),
            "{YYYY}": safe(year),
            "{MM}": safe(month),
            "{DD}": safe(day),
            "{date}": safe(date_text),
            "{title}": safe(title),
            "{id}": safe(video_id),
            "{username}": safe(username_value),
            "{author}": safe(author_value),
            "{quality}": safe(quality or "unknown"),
            "{likes}": safe(str(likes)),
            "{views}": safe(str(views)),
            "{comments}": safe(str(comments)),
            "{duration}": safe(str(duration)),
            "{slug}": safe(slug),
            "{rating}": safe(rating),
        }
        for token, value in mapping.items():
            template = template.replace(token, value)

        parts = [part for part in template.split("/") if part.strip()]
        if not parts:
            parts = [f"{date_text}_{title}_{video_id}.mp4"]

        parts = [self._sanitize_path_segment(part) for part in parts]
        if not parts[-1].lower().endswith(".mp4"):
            parts[-1] += ".mp4"
        parts = self._fit_output_path_to_windows_limit(parts)
        return os.path.join(*parts)

    @classmethod
    def _sanitize_path_segment(cls, name: str) -> str:
        """Return a portable, Windows-safe single path segment."""
        cleaned = re.sub(r'[\x00-\x1f\\/:*?"<>|\x7f]', "-", str(name)).strip(" .")
        if cleaned in ("", ".", ".."):
            return "_"
        stem = cleaned.split(".", 1)[0].rstrip(" ").upper()
        if stem in WINDOWS_RESERVED_FILENAMES:
            cleaned = f"_{cleaned}"
        return cls._shorten_path_segment(cleaned, WINDOWS_SAFE_PATH_LIMIT)

    @staticmethod
    def _windows_path_length(path: str) -> int:
        """Count UTF-16 code units, the length Windows uses for paths."""
        return len(path.encode("utf-16-le")) // 2

    @classmethod
    def _shorten_path_segment(cls, name: str, max_length: int) -> str:
        """Shorten a segment while retaining both its beginning and ending."""
        if cls._windows_path_length(name) <= max_length:
            return name

        stem, extension = os.path.splitext(name)
        digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:10]
        marker = f"-{digest}-"
        available = max(
            1,
            max_length
            - cls._windows_path_length(extension)
            - cls._windows_path_length(marker),
        )
        prefix_length = max(1, available * 2 // 5)
        suffix_length = max(1, available - prefix_length)
        prefix = cls._trim_to_windows_length(stem, prefix_length)
        suffix = cls._trim_to_windows_length(stem, suffix_length, from_end=True)
        return f"{prefix}{marker}{suffix}{extension}"

    @classmethod
    def _fit_output_path_to_windows_limit(cls, parts: list[str]) -> list[str]:
        """Keep output paths usable by Windows and its temporary download files."""
        result = list(parts)
        base_dir = os.path.abspath(app_config.download_dir)
        while cls._windows_path_length(os.path.join(base_dir, *result)) > WINDOWS_SAFE_PATH_LIMIT:
            candidates = [
                (cls._windows_path_length(part), index)
                for index, part in enumerate(result)
                if cls._windows_path_length(part) > WINDOWS_MIN_PATH_SEGMENT_LENGTH
            ]
            if not candidates:
                break
            _, index = max(candidates)
            current_length = cls._windows_path_length(result[index])
            excess = (
                cls._windows_path_length(os.path.join(base_dir, *result))
                - WINDOWS_SAFE_PATH_LIMIT
            )
            target_length = max(
                WINDOWS_MIN_PATH_SEGMENT_LENGTH,
                current_length - excess,
            )
            result[index] = cls._shorten_path_segment(result[index], target_length)
        return result

    @classmethod
    def _trim_to_windows_length(
        cls,
        text: str,
        max_length: int,
        *,
        from_end: bool = False,
    ) -> str:
        chars = reversed(text) if from_end else iter(text)
        kept: list[str] = []
        length = 0
        for char in chars:
            char_length = cls._windows_path_length(char)
            if length + char_length > max_length:
                break
            kept.append(char)
            length += char_length
        if from_end:
            kept.reverse()
        return "".join(kept)
