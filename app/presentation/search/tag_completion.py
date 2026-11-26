"""Tag completion for the keyword box in the "tags" scope (both interfaces).

Iwara takes comma-separated tag names (``core.tag_dictionary``); Oreno3D has
its own query syntax (``core.oreno3d.search_query``).
"""
from __future__ import annotations

from typing import Any

from ...core.oreno3d.search_query import apply_tag_suggestion, tag_suggestion_query
from ...core.tag_dictionary import TagSuggestion, complete_tag_query, tag_query_fragment

SUGGESTION_LIMIT = 12


def suggestion_query(text: str, source: str) -> str:
    """The fragment being typed that suggestions should match ("" for none)."""
    return tag_query_fragment(text) if source == "iwara" else tag_suggestion_query(text)


def complete(text: str, key: str, source: str) -> str:
    """``text`` with the fragment being typed replaced by the chosen tag."""
    return complete_tag_query(text, key) if source == "iwara" else apply_tag_suggestion(text, key)


def suggestions_for(manager: Any, text: str, *, scope: str, source: str, limit: int = SUGGESTION_LIMIT) -> list[TagSuggestion]:
    """Blocking (reads the tag dictionary); empty outside the tags scope."""
    if scope != "tags":
        return []
    query = suggestion_query(text, source)
    if not query:
        return []
    return list(manager.get_search_tag_suggestions(query, limit=limit) or [])


def suggestion_tooltip(suggestion: TagSuggestion) -> str:
    return f"{suggestion.key}\nEN: {suggestion.en}\n中文: {suggestion.zh}\n日本語: {suggestion.ja}"
