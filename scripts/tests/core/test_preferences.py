"""Shared app-wide preference setters save and announce like the old window did."""

from __future__ import annotations

import unittest
from unittest import mock

from app.presentation import preferences


class _Signal:
    def __init__(self):
        self.sent = []

    def emit(self, value):
        self.sent.append(value)


class _Bus:
    def __init__(self):
        self.content_rating_changed = _Signal()
        self.language_changed = _Signal()


class _Config:
    def __init__(self):
        self.ui = {}
        self.ui_language = "zh_CN"
        self.theme_mode = "auto"

    def get_ui_value(self, key, default=None):
        return self.ui.get(key, default)

    def set_ui_value(self, key, value):
        self.ui[key] = value


class PreferenceTests(unittest.TestCase):
    def setUp(self):
        self.bus, self.config = _Bus(), _Config()
        patches = [mock.patch.object(preferences, "_bus", lambda: self.bus), mock.patch.object(preferences, "app_config", self.config)]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_rating_cycles_and_announces(self):
        seen = [preferences.cycle_rating() for _ in range(3)]
        self.assertEqual(seen[-1], preferences.RATING_ALL)
        self.assertEqual(self.bus.content_rating_changed.sent, seen)
        self.assertEqual(len(set(seen)), 3)

    def test_language_codes(self):
        self.assertEqual(preferences.set_language("ja"), "ja_JP")
        self.assertEqual(preferences.set_language("whatever"), "zh_CN")
        self.assertEqual(self.config.ui_language, "zh_CN")
        self.assertEqual(self.bus.language_changed.sent, ["ja_JP", "zh_CN"])

    def test_theme_mode_is_normalized(self):
        self.assertEqual(preferences.set_theme_mode("DARK"), "dark")
        self.assertEqual(preferences.set_theme_mode("neon"), "auto")
        self.assertEqual(self.config.theme_mode, "auto")


if __name__ == "__main__":
    unittest.main()
