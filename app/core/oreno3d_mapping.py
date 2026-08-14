"""Offline Iwara-tag to Oreno3D-entity resolution.

The two sites use different tag systems.  LoveIwara publishes both datasets,
but does not publish a cross-reference, so the checked-in map is generated
from exact localized-name matches and keeps only terms that resolve to one
Oreno3D entity.  Numeric IDs are deliberately not guessed here.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import unicodedata
from dataclasses import dataclass
from typing import Any, Mapping


MAPPING_FILENAME = "oreno3d_iwara_map.json"
MAPPING_BUNDLED_RELATIVE_PATH = os.path.join("data", MAPPING_FILENAME)
_ENTITY_TYPES = frozenset({"tag", "origin", "character"})


def normalize_mapping_key(value: object) -> str:
    """Normalize an Iwara/Oreno display label for a dictionary lookup."""

    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.casefold().strip().lstrip("#").replace("_", " ")
    return " ".join(text.split())


@dataclass(frozen=True, slots=True)
class Oreno3DResolvedEntity:
    """One unambiguous Oreno3D entity addressed by a user-facing term."""

    kind: str
    entity_id: str
    canonical_key: str = ""

    @property
    def route(self) -> str:
        """Return the compact query token used by the search layer."""

        return f"{self.kind}:{self.entity_id}"


def _bundled_mapping_path() -> str | None:
    """Find the mapping in source and Nuitka layouts."""

    module_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    executable_root = os.path.dirname(os.path.abspath(sys.argv[0] or os.curdir))
    candidates = (
        os.path.join(module_root, MAPPING_BUNDLED_RELATIVE_PATH),
        os.path.join(executable_root, "app", MAPPING_BUNDLED_RELATIVE_PATH),
        os.path.join(executable_root, MAPPING_BUNDLED_RELATIVE_PATH),
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


def _parse_entity(value: Any) -> tuple[str, str] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    kind = str(value[0] or "").strip().casefold()
    entity_id = str(value[1] or "").strip()
    if kind not in _ENTITY_TYPES or not entity_id or not re.fullmatch(r"\d+", entity_id):
        return None
    return kind, entity_id


class Oreno3DIwaraMapping:
    """Load and resolve the generated, strict-unique mapping."""

    def __init__(self, path: str | None = None):
        self.path = os.path.abspath(path) if path else _bundled_mapping_path()
        self._entries: dict[str, Oreno3DResolvedEntity] = {}
        self._aliases: dict[str, Oreno3DResolvedEntity] = {}
        self._ambiguous_count = 0
        self._load()

    @property
    def count(self) -> int:
        return len(self._entries)

    @property
    def alias_count(self) -> int:
        return len(self._aliases)

    @property
    def ambiguous_count(self) -> int:
        return self._ambiguous_count

    def resolve(self, value: object) -> Oreno3DResolvedEntity | None:
        """Resolve a display label or canonical Iwara key.

        Pure numbers are left to the caller because they already represent a
        numeric Oreno3D ID and must not be confused with a translated label.
        """

        key = normalize_mapping_key(value)
        if not key or key.isdigit():
            return None
        return self._aliases.get(key)

    def entry_for(self, value: object) -> Oreno3DResolvedEntity | None:
        """Alias for :meth:`resolve` used by search callers."""

        return self.resolve(value)

    def _load(self) -> None:
        if not self.path:
            return
        try:
            with open(self.path, "r", encoding="utf-8") as stream:
                payload = json.load(stream)
        except (OSError, TypeError, ValueError):
            return
        if not isinstance(payload, Mapping):
            return
        counts = payload.get("counts")
        if isinstance(counts, Mapping):
            try:
                self._ambiguous_count = max(0, int(counts.get("ambiguous_iwara_terms", 0)))
            except (TypeError, ValueError):
                self._ambiguous_count = 0

        entry_fragments: list[Mapping[str, Any]] = []
        raw_entries = payload.get("entries")
        if isinstance(raw_entries, Mapping):
            entry_fragments.append(raw_entries)
        entry_fragments.extend(self._read_fragments(payload.get("entry_files"), "entries"))
        for fragment in entry_fragments:
            for raw_key, raw_value in fragment.items():
                key = str(raw_key or "").strip()
                entity = _parse_entity(raw_value)
                if not key or entity is None:
                    continue
                self._entries[key] = Oreno3DResolvedEntity(*entity, canonical_key=key)

        alias_fragments: list[Mapping[str, Any]] = []
        raw_aliases = payload.get("aliases")
        if isinstance(raw_aliases, Mapping):
            alias_fragments.append(raw_aliases)
        alias_fragments.extend(self._read_fragments(payload.get("alias_files"), "aliases"))
        for fragment in alias_fragments:
            for raw_alias, raw_value in fragment.items():
                alias = normalize_mapping_key(raw_alias)
                entity = _parse_entity(raw_value)
                if not alias or entity is None:
                    continue
                canonical = self._canonical_for(entity)
                self._aliases[alias] = Oreno3DResolvedEntity(
                    *entity,
                    canonical_key=canonical,
                )

        # A malformed/older map may omit aliases.  Canonical keys are always
        # safe to add because entries were generated as unique terms.
        for key, entity in self._entries.items():
            self._aliases.setdefault(normalize_mapping_key(key), entity)

    def _read_fragments(
        self,
        names: Any,
        key: str,
    ) -> list[Mapping[str, Any]]:
        """Read optional split resource files declared by the main manifest."""

        if not isinstance(names, (list, tuple)) or not self.path:
            return []
        directory = os.path.dirname(self.path)
        fragments: list[Mapping[str, Any]] = []
        for raw_name in names:
            name = str(raw_name or "").strip()
            if not name:
                continue
            try:
                with open(os.path.join(directory, name), "r", encoding="utf-8") as stream:
                    fragment = json.load(stream)
            except (OSError, TypeError, ValueError):
                continue
            values = fragment.get(key) if isinstance(fragment, Mapping) else None
            if isinstance(values, Mapping):
                fragments.append(values)
        return fragments

    def _canonical_for(self, entity: tuple[str, str]) -> str:
        for key, value in self._entries.items():
            if (value.kind, value.entity_id) == entity:
                return key
        return ""


_mapping_lock = threading.Lock()
_mapping: Oreno3DIwaraMapping | None = None


def get_oreno3d_iwara_mapping() -> Oreno3DIwaraMapping:
    """Return the process-wide immutable mapping instance."""

    global _mapping
    if _mapping is None:
        with _mapping_lock:
            if _mapping is None:
                _mapping = Oreno3DIwaraMapping()
    return _mapping


def resolve_oreno3d_entity(value: object) -> Oreno3DResolvedEntity | None:
    """Resolve one label using the bundled process-wide dictionary."""

    return get_oreno3d_iwara_mapping().resolve(value)
