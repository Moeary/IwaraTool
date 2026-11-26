"""The new interface's settings groups (presentation/settings/groups.py) lose no Classic card."""
from __future__ import annotations

import unittest

from app.presentation.settings import catalog, groups


class GroupTests(unittest.TestCase):
    def test_every_classic_card_has_a_group(self):
        classic = {card for c in catalog.CATEGORIES for card in c.cards}
        grouped = {card for g in groups.groups() for card in g.cards}
        self.assertLessEqual(classic, grouped)
        self.assertIn("rating", grouped)
        self.assertIn("about", grouped)

    def test_old_links_land_on_a_group(self):
        for key in [c.key for c in catalog.CATEGORIES] + ["account", "proxy", "site", "aria2", "ffmpeg", "updates"]:
            self.assertIsNotNone(groups.find(key), key)
        self.assertEqual(groups.find("tools").key, "system")
        self.assertIsNone(groups.find("shortcuts", desktop=False))
        self.assertEqual(groups.visible(desktop=True)[-1].key, "about")


if __name__ == "__main__":
    unittest.main()
