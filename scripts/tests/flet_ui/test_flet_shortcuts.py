"""Flet key events map onto the shared shortcut catalogue."""

from __future__ import annotations

import unittest
from unittest import mock

from app.presentation import shortcuts as catalog
from app.ui_flet.shortcuts import ShortcutMap, event_chord


class ChordTests(unittest.TestCase):
    def test_command_is_ctrl_on_macos(self):
        self.assertEqual(event_chord("R", meta=True, shift=True, mac=True), "Ctrl+Shift+R")
        self.assertEqual(event_chord("F", ctrl=True, meta=True, mac=True), "Meta+Ctrl+F")
        self.assertEqual(event_chord("r", ctrl=True, shift=True, mac=False), "Ctrl+Shift+R")

    def test_named_keys_and_modifiers_alone(self):
        self.assertEqual(event_chord("Escape"), "Esc")
        self.assertEqual(event_chord("Arrow Left", alt=True, mac=False), "Alt+Left")
        self.assertEqual(event_chord("F11", mac=False), "F11")
        self.assertEqual(event_chord("Shift Left", shift=True), "")
        self.assertEqual(event_chord("Launch Mail"), "")

    def test_chords_match_the_catalogue_normalization(self):
        for chord in ("Ctrl+Shift+R", "Esc", "Ctrl+1", "Alt+Left", "Meta+Ctrl+F"):
            self.assertEqual(catalog.portable_normalize(chord), chord)


class MapTests(unittest.TestCase):
    def test_defaults_and_overrides(self):
        with mock.patch.object(catalog, "load_overrides", return_value={"nav_home": "Ctrl+Shift+H"}):
            shortcuts = ShortcutMap()
        self.assertEqual(shortcuts.action_for("Ctrl+Shift+H"), "nav_home")
        self.assertEqual(shortcuts.action_for("Ctrl+1"), "")
        self.assertEqual(shortcuts.action_for("Ctrl+Shift+R"), "rating_cycle")
        self.assertEqual(shortcuts.action_for("Esc"), "nav_back")


if __name__ == "__main__":
    unittest.main()
