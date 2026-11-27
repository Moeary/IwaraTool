"""Card columns and the list-detail width rules (components/card_columns.py, list_detail.py)."""
from __future__ import annotations

import importlib.util
import unittest
from unittest import mock

HAS_FLET = importlib.util.find_spec("flet") is not None


@unittest.skipUnless(HAS_FLET, "Flet is not installed in this environment")
class LayoutHelperTests(unittest.TestCase):
    def test_two_columns_only_when_two_cards_fit(self):
        from app.ui.components.card_columns import MIN_CARD, column_count

        self.assertEqual(column_count(2 * MIN_CARD - 1), 1)
        self.assertEqual(column_count(2 * MIN_CARD + 100), 2)

    def test_saved_width_is_clamped(self):
        from app.ui.components import list_detail

        config = mock.Mock()
        config.get_ui_value.return_value = "9999"
        with mock.patch.object(list_detail, "app_config", config):
            self.assertEqual(list_detail.saved_width("k", 248, 200, 420), 420)
        config.get_ui_value.return_value = "x"
        with mock.patch.object(list_detail, "app_config", config):
            self.assertEqual(list_detail.saved_width("k", 248, 200, 420), 248)


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(HAS_FLET, "Flet is not installed in this environment")
class ButtonRowTests(unittest.TestCase):
    def test_buttons_share_balanced_lines(self):
        from app.ui.components.button_row import lines_of, per_line

        self.assertEqual(per_line(1000, 1), 1)
        self.assertEqual(per_line(0, 4), 2)  # before the row has measured itself
        self.assertEqual(per_line(400, 3), 2)  # two fit: a pair, then the third on its own
        self.assertEqual(per_line(500, 4), 2)  # three fit: two lines of two, not three and one
        self.assertEqual(per_line(700, 4), 4)
        self.assertEqual(per_line(120, 2), 1)
        self.assertEqual(lines_of([1, 2, 3], 2), [[1, 2], [3]])
