"""Keyboard shortcut catalogue for PySide6: ``presentation.shortcuts`` plus QKeySequence.

The actions, defaults, overrides and conflict rules live in
``app.presentation.shortcuts``; this module makes them parse and display
keys with ``QKeySequence`` (so the PySide6 interface behaves as before) and
re-exports the rest.
"""
from __future__ import annotations

from PySide6.QtGui import QKeySequence

from ...presentation import shortcuts as _catalog
from ...presentation.shortcuts import (  # noqa: F401 - re-exported
    IS_MAC,
    PLATFORM_ONLY,
    SCOPE_DOWNLOAD,
    SCOPE_GLOBAL,
    SCOPE_HISTORY,
    SCOPE_HOME,
    SCOPE_PLAYER,
    SCOPE_REPAIR,
    SCOPE_RULES,
    SCOPE_SEARCH,
    SCOPE_SETTINGS,
    SCOPE_SUBSCRIPTIONS,
    ShortcutAction,
    actions_by_id,
    all_actions,
    find_conflict,
    key_for,
    load_overrides,
    normalize,
    reset_all,
    reset_key,
    scope_titles,
    scopes_overlap,
    set_key,
)


def _qt_normalize(text: str) -> str:
    parsed = QKeySequence(text, QKeySequence.SequenceFormat.PortableText)
    return parsed.toString(QKeySequence.SequenceFormat.PortableText) if not parsed.isEmpty() else ""


_catalog.set_normalizer(_qt_normalize)


def display_key(sequence: str) -> str:
    """``sequence`` as this platform writes it: "⌘K" on macOS, "Ctrl+K" elsewhere."""

    text = normalize(sequence)
    if not text:
        return ""
    return QKeySequence(text, QKeySequence.SequenceFormat.PortableText).toString(
        QKeySequence.SequenceFormat.NativeText
    )


def display_key_for(action_id: str) -> str:
    return display_key(key_for(action_id))
