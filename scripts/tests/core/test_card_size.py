"""The shared cover width (presentation/media/card_size.py), used by both front ends."""
from __future__ import annotations

import unittest
from unittest import mock

from app.presentation.media import card_size


class _Config:
    def __init__(self, value=None):
        self.ui = {} if value is None else {card_size.CARD_WIDTH_KEY: value}

    def get_ui_value(self, key, default=""):
        return self.ui.get(key, default)

    def set_ui_value(self, key, value, *, sync=True):
        self.ui[key] = value


class CardSizeTests(unittest.TestCase):
    def test_clamped_and_stepped(self):
        self.assertEqual(card_size.clamp_card_width(10), card_size.CARD_WIDTH_MIN)
        self.assertEqual(card_size.clamp_card_width(9999), card_size.CARD_WIDTH_MAX)
        self.assertEqual(card_size.clamp_card_width("x"), card_size.CARD_WIDTH_DEFAULT)
        self.assertEqual(card_size.stepped_card_width(224, 1), 224 + card_size.CARD_WIDTH_STEP)
        self.assertEqual(card_size.stepped_card_width(card_size.CARD_WIDTH_MIN, -3), card_size.CARD_WIDTH_MIN)

    def test_saved_value_round_trip(self):
        config = _Config("bad")
        with mock.patch.object(card_size, "app_config", config):
            self.assertEqual(card_size.saved_card_width(), card_size.CARD_WIDTH_DEFAULT)
            self.assertEqual(card_size.save_card_width(1000), card_size.CARD_WIDTH_MAX)
            self.assertEqual(card_size.saved_card_width(), card_size.CARD_WIDTH_MAX)


if __name__ == "__main__":
    unittest.main()
