"""Records parsed from Oreno3D pages: entities, listing cards and video details."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True, frozen=True)
class Oreno3DEntity:
    source_id: str
    name: str
    url: str


@dataclass(slots=True)
class Oreno3DListing:
    source_id: str
    oreno3d_url: str
    title: str
    author_name: str
    thumbnail_url: str
    view_count: int | None
    favorite_count: int | None
    tags: tuple[str, ...] = ()


@dataclass(slots=True)
class Oreno3DDetail:
    source_id: str
    oreno3d_url: str
    title: str
    external_video_url: str
    author: Oreno3DEntity | None
    tags: list[Oreno3DEntity] = field(default_factory=list)
    origins: list[Oreno3DEntity] = field(default_factory=list)
    characters: list[Oreno3DEntity] = field(default_factory=list)
    thumbnail_url: str = ""
    published_at: str = ""
    view_count: int | None = None
    favorite_count: int | None = None
    author_comment: str = ""
