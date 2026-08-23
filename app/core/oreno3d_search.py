"""Search request helpers for the Oreno3D-to-Iwara bridge.

Oreno3D has two search surfaces: ``/search`` accepts free text, while a
known entity is searched through ``/tags/{id}``, ``/origins/{id}``, or
``/characters/{id}``.  Keeping the small amount of query grammar here keeps
the UI and the HTTP client independent from each other.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any, Sequence
from urllib.parse import unquote, urlparse

from .oreno3d_mapping import resolve_oreno3d_entity


_ENTITY_PATHS = {
    "tag": "tags",
    "tags": "tags",
    "origin": "origins",
    "origins": "origins",
    "character": "characters",
    "characters": "characters",
}
_TAG_QUERY_PREFIXES = frozenset({"tag", "tags", "origin", "origins", "character", "characters"})


@dataclass(frozen=True, slots=True)
class Oreno3DSearchQuery:
    """One normalized Oreno3D search request.

    ``search_type`` and ``entity_id`` are both set only for a direct entity
    search.  A free-text search keeps them empty and is sent to ``/search``.
    """

    keyword: str = ""
    search_type: str = ""
    entity_id: str = ""

    @property
    def is_entity_search(self) -> bool:
        return bool(self.search_type and self.entity_id)


def _clean(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def _entity_query(search_type: str, entity_id: str) -> Oreno3DSearchQuery:
    normalized_type = _clean(search_type).casefold()
    normalized_id = unquote(_clean(entity_id)).strip().strip("/")
    path_kind = _ENTITY_PATHS.get(normalized_type, "")
    if not path_kind or not normalized_id:
        return Oreno3DSearchQuery(keyword=_clean(entity_id))
    canonical_type = {
        "tags": "tag",
        "origins": "origin",
        "characters": "character",
    }[path_kind]
    return Oreno3DSearchQuery(
        keyword="",
        search_type=canonical_type,
        entity_id=normalized_id,
    )


def _mapped_entity_query(search_type: str, entity_id: str) -> Oreno3DSearchQuery | None:
    """Resolve a human label while retaining numeric IDs as-is."""

    value = _clean(entity_id)
    if not value or value.isdigit():
        return None
    resolved = resolve_oreno3d_entity(value)
    if resolved is None:
        return None
    return _entity_query(resolved.kind, resolved.entity_id)


def parse_oreno3d_query(value: str, *, scope: str = "videos") -> Oreno3DSearchQuery:
    """Parse the compact query grammar used by the search page.

    Supported forms are ``tag:genshin``, ``origin:game-id``,
    ``character:traveler``, and an Oreno3D entity URL.  In the ``tags`` scope,
    a single plain term is treated as a tag entity; multiple terms remain a
    free-text query so users can still search combined labels.
    """

    text = _clean(value)
    if not text:
        return Oreno3DSearchQuery()

    parsed = urlparse(text)
    hostname = (parsed.hostname or "").casefold()
    if hostname in {"oreno3d.com", "www.oreno3d.com"}:
        parts = [unquote(part).strip() for part in parsed.path.split("/") if part]
        if len(parts) >= 2:
            kind = parts[-2].casefold()
            if kind in _ENTITY_PATHS:
                return _entity_query(kind, parts[-1])

    if ":" in text:
        prefix, entity_id = text.split(":", 1)
        if prefix.casefold() in _ENTITY_PATHS and _clean(entity_id):
            return _mapped_entity_query(prefix, entity_id) or _entity_query(prefix, entity_id)

    if (
        (scope == "tags" or text.startswith("#"))
        and "," not in text
        and "，" not in text
        and " " not in text
    ):
        value = text.lstrip("#")
        return _mapped_entity_query("tag", value) or _entity_query("tag", value)
    return Oreno3DSearchQuery(keyword=text)


def map_oreno3d_sort(sort: str) -> str:
    """Map the app's stable sort keys to Oreno3D's known sort names."""

    return {
        "date": "latest",
        "trending": "hot",
        "popularity": "popularity",
        "views": "views",
        "likes": "favorites",
    }.get(_clean(sort).casefold(), "latest")


def parse_oreno3d_tag_ids(value: str) -> tuple[str, ...]:
    """Return explicit tag ids from a tag-scope input.

    Numeric Oreno3D tag IDs have direct entity routes; names are resolved by
    the public keyword endpoint.  This helper deliberately accepts only tag
    terms; an ``origin:`` or ``character:`` expression is left to the normal
    single-query parser instead of being silently misinterpreted as a tag.
    """

    terms = re.split(r"[,，;；|\s]+", str(value or "").strip())
    result: list[str] = []
    seen: set[str] = set()
    for raw in terms:
        token = raw.strip()
        if not token:
            continue
        if ":" in token:
            prefix, token = token.split(":", 1)
            if prefix.casefold() not in {"tag", "tags"}:
                return ()
        token = token.lstrip("#").strip()
        if not token:
            continue
        key = token.casefold()
        if key not in seen:
            seen.add(key)
            result.append(token)
    return tuple(result)


def tag_suggestion_query(value: str) -> str:
    """Extract the current autocomplete token without losing ``tag:``."""

    match = re.search(r"([^,，;；|\s]*)$", str(value or ""))
    if not match:
        return ""
    token = match.group(1)
    if ":" not in token:
        return token.strip()
    prefix, query = token.split(":", 1)
    return query.strip() if prefix.casefold() in _TAG_QUERY_PREFIXES else token.strip()


def apply_tag_suggestion(value: str, suggestion: str) -> str:
    """Replace the current autocomplete token while preserving its prefix."""

    text = str(value or "")
    match = re.search(r"([^,，;；|\s]*)$", text)
    if not match:
        return text
    token = match.group(1)
    marker = ""
    if ":" in token:
        prefix, _query = token.split(":", 1)
        if prefix.casefold() in _TAG_QUERY_PREFIXES:
            marker = f"{prefix}:"
    head = text[: match.start()].rstrip(" ,，;；|")
    return f"{head + ', ' if head else ''}{marker}{str(suggestion or '').strip()}, "


def intersect_oreno3d_listings(groups: Sequence[Sequence[Any]]) -> list[Any]:
    """Intersect listing cards by movie id and merge their visible tags."""

    if not groups or any(not group for group in groups):
        return []
    indexes: list[dict[str, Any]] = []
    for group in groups:
        index = {
            str(getattr(item, "source_id", "") or "").casefold(): item
            for item in group
            if str(getattr(item, "source_id", "") or "").strip()
        }
        indexes.append(index)
    first_group = groups[0]
    result: list[Any] = []
    for item in first_group:
        key = str(getattr(item, "source_id", "") or "").casefold()
        if not key or any(key not in index for index in indexes[1:]):
            continue
        merged_tags: list[str] = []
        seen_tags: set[str] = set()
        for index in indexes:
            for tag in getattr(index[key], "tags", ()) or ():
                tag_text = str(tag or "").strip()
                if tag_text and tag_text.casefold() not in seen_tags:
                    seen_tags.add(tag_text.casefold())
                    merged_tags.append(tag_text)
        result.append(replace(item, tags=tuple(merged_tags)))
    return result
