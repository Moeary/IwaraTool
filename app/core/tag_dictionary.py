"""Multi-language Iwara tag dictionary and autocomplete helpers.

The LoveIwara dictionary is shipped as an application resource, expanded into
the runtime ``data`` directory on first launch, and refreshed there on demand.
The application also understands the project's generated
``data/iwara_tags.json`` so tag suggestions remain available offline on
existing installations.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Mapping

from ..config import app_config


LOVEIWARA_TAGS_URL = (
    "https://raw.githubusercontent.com/FoxSensei001/LoveIwara/"
    "master/tool/data/iwara_tags/iwara_tags_localized.json"
)
LOVEIWARA_TAGS_SOURCE = "FoxSensei001/LoveIwara tool/data/iwara_tags/iwara_tags_localized.json"
LOVEIWARA_TAGS_FILENAME = "loveiwara_iwara_tags_localized.json"
LOVEIWARA_TAGS_BUNDLED_RELATIVE_PATH = os.path.join(
    "data", "tag_translations", LOVEIWARA_TAGS_FILENAME
)


def _bundled_tag_path() -> str | None:
    """Return the bundled dictionary path in source and Nuitka layouts."""

    module_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    executable_root = os.path.dirname(os.path.abspath(sys.argv[0] or os.curdir))
    candidates = (
        os.path.join(module_root, LOVEIWARA_TAGS_BUNDLED_RELATIVE_PATH),
        os.path.join(executable_root, "app", LOVEIWARA_TAGS_BUNDLED_RELATIVE_PATH),
        os.path.join(executable_root, LOVEIWARA_TAGS_BUNDLED_RELATIVE_PATH),
    )
    seen: set[str] = set()
    for candidate in candidates:
        candidate = os.path.abspath(candidate)
        if candidate in seen:
            continue
        seen.add(candidate)
        if os.path.isfile(candidate):
            return candidate
    return None


@dataclass(frozen=True, slots=True)
class TagSuggestion:
    """A canonical tag key and its localized display names."""

    key: str
    en: str
    zh: str
    ja: str

    @property
    def display_text(self) -> str:
        labels: list[str] = []
        for value in (self.en, self.zh, self.ja):
            value = str(value or "").strip()
            if value and value not in labels:
                labels.append(value)
        return f"{self.key}  ·  " + " / ".join(labels)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _norm(value: Any) -> str:
    return unicodedata.normalize("NFKC", _text(value)).casefold().strip()


class TagDictionary:
    """Load, merge, and query localized tag dictionaries."""

    def __init__(self, data_dir: str | None = None):
        self.data_dir = os.path.abspath(data_dir or app_config.app_data_dir)
        self.cache_path = os.path.join(
            self.data_dir,
            "tag_translations",
            LOVEIWARA_TAGS_FILENAME,
        )
        self._entries: dict[str, TagSuggestion] = {}
        self._aliases: dict[str, str] = {}
        self._lock = threading.RLock()
        self._ensure_bundled_cache()
        self.reload()

    def _ensure_bundled_cache(self) -> None:
        """Expand the packaged dictionary without replacing user data."""

        try:
            if os.path.isfile(self.cache_path) and os.path.getsize(self.cache_path) > 0:
                return
        except OSError:
            pass

        source_path = _bundled_tag_path()
        if not source_path or os.path.abspath(source_path) == os.path.abspath(self.cache_path):
            return

        temp_path = f"{self.cache_path}.{os.getpid()}.{threading.get_ident()}.bundle.tmp"
        try:
            os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
            shutil.copyfile(source_path, temp_path)
            os.replace(temp_path, self.cache_path)
        except OSError:
            # A read-only data directory should not prevent the application from
            # using the bundled resource or the generated tag cache.
            pass
        finally:
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except OSError:
                pass

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._entries)

    def reload(self) -> int:
        """Reload cached LoveIwara data and the project's generated tags."""

        entries: dict[str, TagSuggestion] = {}
        # The remote dictionary has richer translations, so load it first.
        for path in (self.cache_path, os.path.join(self.data_dir, "iwara_tags.json")):
            raw = self._read_json(path)
            for suggestion in self._parse(raw):
                current = entries.get(suggestion.key)
                if current is None:
                    entries[suggestion.key] = suggestion
                else:
                    entries[suggestion.key] = TagSuggestion(
                        key=suggestion.key,
                        en=current.en or suggestion.en,
                        zh=current.zh or suggestion.zh,
                        ja=current.ja or suggestion.ja,
                    )
        aliases: dict[str, str] = {}
        for key, suggestion in entries.items():
            for value in (key, suggestion.en, suggestion.zh, suggestion.ja):
                normalized = _norm(value)
                if normalized:
                    aliases.setdefault(normalized, key)
        with self._lock:
            self._entries = entries
            self._aliases = aliases
            return len(entries)

    def suggest(self, query: str, *, limit: int = 16) -> list[TagSuggestion]:
        """Return multilingual candidates ranked by exact/prefix/substring match."""

        query_norm = _norm(query).lstrip("#")
        with self._lock:
            values = list(self._entries.values())
        if not query_norm:
            return values[: max(1, limit)]

        ranked: list[tuple[tuple[int, float, int, str], TagSuggestion]] = []
        for item in values:
            fields = [_norm(item.key), _norm(item.en), _norm(item.zh), _norm(item.ja)]
            best = 0
            similarity = 0.0
            for field in fields:
                if not field:
                    continue
                if field == query_norm:
                    best = max(best, 4)
                elif field.startswith(query_norm):
                    best = max(best, 3)
                elif query_norm in field:
                    best = max(best, 2)
                else:
                    similarity = max(similarity, SequenceMatcher(None, query_norm, field).ratio())
            if best or similarity >= 0.45:
                ranked.append(((best, similarity, -len(item.key), item.key), item))
        ranked.sort(key=lambda value: value[0], reverse=True)
        return [item for _, item in ranked[: max(1, limit)]]

    def canonical_key(self, value: str) -> str:
        """Translate an English/Chinese/Japanese label to its canonical key."""

        normalized = _norm(value).lstrip("#")
        if not normalized:
            return ""
        with self._lock:
            return self._aliases.get(normalized, _text(value).lstrip("#").strip())

    def entry_for(self, value: str) -> TagSuggestion | None:
        """Return the localized entry addressed by a key or translated label."""

        normalized = _norm(value).lstrip("#")
        if not normalized:
            return None
        with self._lock:
            key = self._aliases.get(normalized)
            return self._entries.get(key) if key else None

    def update_from_remote(self, session, *, timeout: int = 30) -> tuple[int, str]:
        """Download the MIT-licensed LoveIwara dictionary atomically."""

        response = None
        temp_path = f"{self.cache_path}.{threading.get_ident()}.tmp"
        try:
            response = session.get(
                LOVEIWARA_TAGS_URL,
                headers={"Accept": "application/json", "Referer": "https://github.com/"},
                timeout=timeout,
            )
            if int(getattr(response, "status_code", 0) or 0) != 200:
                return 0, f"HTTP {getattr(response, 'status_code', 0)}"
            payload = response.json()
            parsed = self._parse(payload)
            if len(parsed) < 100:
                return 0, "The downloaded tag dictionary is too small"
            os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
            with open(temp_path, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
            os.replace(temp_path, self.cache_path)
            count = self.reload()
            return count, ""
        except Exception as exc:
            return 0, str(exc)
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except OSError:
                pass

    @staticmethod
    def _read_json(path: str) -> Any:
        try:
            with open(path, "r", encoding="utf-8") as stream:
                return json.load(stream)
        except (OSError, ValueError, TypeError):
            return None

    @staticmethod
    def _parse(raw: Any) -> list[TagSuggestion]:
        if isinstance(raw, Mapping) and isinstance(raw.get("tags"), list):
            raw = raw["tags"]
        result: list[TagSuggestion] = []
        if isinstance(raw, Mapping):
            items = raw.items()
        elif isinstance(raw, list):
            items = (("", value) for value in raw)
        else:
            return result
        seen: set[str] = set()
        for raw_key, raw_value in items:
            if isinstance(raw_value, Mapping):
                key = _text(raw_value.get("key") or raw_value.get("slug") or raw_key)
                en = _text(raw_value.get("en") or raw_value.get("name_en") or raw_value.get("name"))
                zh = _text(raw_value.get("zh-CN") or raw_value.get("zh") or raw_value.get("name_zh"))
                ja = _text(raw_value.get("ja") or raw_value.get("name_ja"))
            else:
                key = _text(raw_key)
                en = _text(raw_value)
                zh = ""
                ja = ""
            key = key.lstrip("#").strip()
            if not key or _norm(key) in seen:
                continue
            seen.add(_norm(key))
            result.append(TagSuggestion(key=key, en=en or key, zh=zh or en or key, ja=ja or en or key))
        return result
