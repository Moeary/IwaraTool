"""Snapshot of a shown post for back / forward navigation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ...core.search import SearchVideo


@dataclass
class DetailState:
    """A post as it was shown: the API answers, the comments opened so far and the scroll.

    Restoring rebuilds the page from these answers, so Back to a post neither
    asks the network again nor loses the expanded replies or the position.
    """

    kind: str
    item_id: str
    video: SearchVideo | None = None
    info: dict[str, Any] | None = None
    related: list[SearchVideo] | None = None
    comment_batches: list[tuple[list[dict[str, Any]], int | None, str]] = field(default_factory=list)
    comment_page: int = 0
    replies: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    liked: bool = False
    scroll: int = 0

    def __eq__(self, other: object) -> bool:  # same post = same place in the history
        return isinstance(other, DetailState) and (self.kind, self.item_id) == (other.kind, other.item_id)
