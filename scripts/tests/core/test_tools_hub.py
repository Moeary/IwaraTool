"""The Tools hub list (presentation/tools/hub.py): rules first, repair second, then the upload tools."""
from __future__ import annotations

import unittest

from app.presentation.tools import common, hub


class HubTests(unittest.TestCase):
    def test_order_and_phones(self):
        keys = [e.key for e in hub.entries(desktop=True)]
        self.assertEqual(keys, ["rules", "repair", *[t.key for t in common.tools()]])
        self.assertEqual([e.key for e in hub.entries(desktop=False)], ["rules"])
        self.assertTrue(all(e.group in hub.group_titles() for e in hub.entries()))


if __name__ == "__main__":
    unittest.main()
