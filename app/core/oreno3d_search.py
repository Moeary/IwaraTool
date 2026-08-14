"""Search request helpers for the Oreno3D-to-Iwara bridge.

Oreno3D has two search surfaces: ``/search`` accepts free text, while a
known entity is searched through ``/tags/{id}``, ``/origins/{id}``, or
``/characters/{id}``.  Keeping the small amount of query grammar here keeps
the UI and the HTTP client independent from each other.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import unquote, urlparse


_ENTITY_PATHS = {
    "tag": "tags",
    "tags": "tags",
    "origin": "origins",
    "origins": "origins",
    "character": "characters",
    "characters": "characters",
}


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
            return _entity_query(prefix, entity_id)

    if (
        (scope == "tags" or text.startswith("#"))
        and "," not in text
        and "，" not in text
        and " " not in text
    ):
        return _entity_query("tag", text.lstrip("#"))
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
