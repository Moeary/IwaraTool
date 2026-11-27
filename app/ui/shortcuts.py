"""Keyboard shortcuts of the Flet interface (desktop).

The catalogue, the user's overrides and the portable key text are
``presentation.shortcuts``'s.  This module turns a Flet ``KeyboardEvent`` into
that text (``Ctrl`` means Command on macOS and ``Meta`` the Control key) and
names the actions the shell handles (``HANDLED``): the page-level actions of
the catalogue are not wired up yet, so Settings lists only these.
Multi-chord sequences ("Ctrl+K, Ctrl+S") are not matched yet.
"""
from __future__ import annotations

from ..presentation import shortcuts as catalog

# What ``Shell.run_action`` does; keep in step with it (scripts/tests/ui/test_shortcuts.py).
NAVIGATION = ("nav_home", "nav_subscriptions", "nav_search", "nav_download", "nav_history", "nav_rules",
              "nav_repair", "nav_tools", "nav_settings", "open_settings")
HANDLED = frozenset(NAVIGATION + (
    "nav_back", "nav_next", "nav_prev", "rating_cycle", "card_size_up", "card_size_down", "card_size_reset",
    "toggle_site", "toggle_theme", "toggle_fullscreen", "minimize_window", "close_window", "quit_app",
))

# Flutter's key labels → the catalogue's key names.
_KEY_NAMES = {
    "escape": "Esc",
    "arrow left": "Left",
    "arrow right": "Right",
    "arrow up": "Up",
    "arrow down": "Down",
    "page up": "PgUp",
    "page down": "PgDown",
    "delete": "Del",
    "insert": "Ins",
    "enter": "Return",
    "numpad enter": "Enter",
    " ": "Space",
    "space": "Space",
    "backspace": "Backspace",
    "tab": "Tab",
    "home": "Home",
    "end": "End",
    "context menu": "Menu",
}
_MODIFIER_KEYS = frozenset({"shift", "shift left", "shift right", "control", "control left", "control right",
                            "alt", "alt left", "alt right", "meta", "meta left", "meta right"})


def event_chord(key: str, *, shift: bool = False, ctrl: bool = False, alt: bool = False, meta: bool = False,
                mac: bool = catalog.IS_MAC) -> str:
    """Portable text of one key press (``""`` for a lone modifier or an unknown key)."""

    raw = str(key or "")
    folded = raw.casefold()
    if not raw or folded in _MODIFIER_KEYS:
        return ""
    if folded in _KEY_NAMES:
        name = _KEY_NAMES[folded]
    elif len(raw) == 1:
        name = raw.upper()
    elif folded.startswith("f") and folded[1:].isdigit():
        name = raw.upper()
    else:
        return ""
    command, control = (meta, ctrl) if mac else (ctrl, meta)
    parts = [label for label, on in (("Meta", control), ("Ctrl", command), ("Alt", alt), ("Shift", shift)) if on]
    return "+".join([*parts, name])


class ShortcutMap:
    """Chord → action id for one set of scopes; ``refresh()`` after the user rebinds keys."""

    def __init__(self, scopes: tuple[str, ...] = (catalog.SCOPE_GLOBAL,)):
        self._scopes = scopes
        self._by_chord: dict[str, str] = {}
        self.refresh()

    def refresh(self) -> None:
        mapping: dict[str, str] = {}
        for action in catalog.all_actions():
            if action.scope not in self._scopes:
                continue
            sequence = catalog.key_for(action.id)
            if sequence and ", " not in sequence:
                mapping.setdefault(sequence, action.id)
        self._by_chord = mapping

    def action_for(self, chord: str) -> str:
        return self._by_chord.get(chord, "") if chord else ""
