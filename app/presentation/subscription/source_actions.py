"""Subscription actions shared by both interfaces (blocking: run them off the UI thread)."""
from __future__ import annotations

from typing import Any

from ...core.site import site_of_url, site_scope
from .helpers import _detect_source_input


def add_source_from_text(manager: Any, text: str) -> int | None:
    """Add the author / playlist named by ``text``: its source id, 0 when not added, ``None`` for bad input.

    A pasted iwara.ai link subscribes on that site.
    """
    text = str(text or "").strip()
    kind, key = _detect_source_input(text) if text else ("", "")
    if not key:
        return None
    with site_scope(site_of_url(text)):
        if kind == "playlist":
            return int(manager.add_playlist_subscription(key) or 0)
        return int(manager.add_author_subscription(key) or 0)


def remove_sources(manager: Any, source_ids: list[int]) -> None:
    remove_many = getattr(manager, "remove_subscription_sources", None)
    if callable(remove_many):
        remove_many(source_ids)
        return
    for source_id in source_ids:
        manager.remove_subscription_source(source_id)
