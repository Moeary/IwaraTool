"""LayoutState and grid maths over the size checklist of docs/flet_ui_plan.md 9.2."""

from __future__ import annotations

import unittest

from app.ui import layout
from app.ui.layout import LayoutState, grid_metrics

# (width, height, platform) -> expected navigation
CHECKLIST = {
    (320, 640, "android"): layout.NAV_BAR,
    (344, 882, "android"): layout.NAV_BAR,
    (360, 800, "android"): layout.NAV_BAR,
    (411, 960, "android"): layout.NAV_BAR,
    (960, 411, "android"): layout.NAV_RAIL_COLLAPSED,  # 21:9 landscape: height wins
    (673, 841, "android"): layout.NAV_RAIL,
    (841, 673, "android"): layout.NAV_RAIL,
    (800, 1280, "android"): layout.NAV_RAIL,
    (1280, 800, "android"): layout.NAV_RAIL_EXTENDED,
    (1440, 900, "macos"): layout.NAV_RAIL_EXTENDED,
    (2560, 1440, "windows"): layout.NAV_RAIL_EXTENDED,
}


class SizeClassTests(unittest.TestCase):
    def test_width_and_height_classes(self):
        self.assertEqual([layout.width_class(w) for w in (0, 599, 600, 839, 840, 1199, 1200, 1599, 1600)],
                         ["compact", "compact", "medium", "medium", "expanded", "expanded", "large", "large", "xlarge"])
        self.assertEqual([layout.height_class(h) for h in (479, 480, 899, 900)], ["compact", "medium", "medium", "expanded"])

    def test_navigation_over_the_checklist(self):
        for (w, h, platform), expected in CHECKLIST.items():
            with self.subTest(size=(w, h)):
                self.assertEqual(LayoutState(w, h, platform).nav_mode, expected)

    def test_collapsed_preference_and_content_width(self):
        wide = LayoutState(1440, 900, "macos", rail_extended_preference=False)
        self.assertEqual(wide.nav_mode, layout.NAV_RAIL)
        self.assertEqual(LayoutState(400, 800, "android").content_width, 400)
        self.assertLess(LayoutState(1440, 900).content_width, 1440 - 200)

    def test_dragged_rail_width(self):
        dragged = LayoutState(1000, 800, "macos", rail_width_preference=300)
        self.assertEqual(dragged.nav_mode, layout.NAV_RAIL_EXTENDED)  # stays wide on a 1000-wide window
        self.assertEqual(dragged.nav_width, 300)
        self.assertEqual(LayoutState(1000, 800, "macos").nav_mode, layout.NAV_RAIL)  # never dragged: as before
        self.assertEqual(LayoutState(1440, 900, rail_width_preference=999).nav_width, layout.RAIL_WIDTH_RANGE[1])
        self.assertEqual(dragged.content_width, 1000 - 300 - layout.RAIL_HANDLE)
        self.assertEqual(LayoutState(1000, 800, "android").rail_handle, 1)  # tablets: a plain rule

    def test_two_panes_from_840_content_width(self):
        self.assertFalse(LayoutState(841, 673, "android").two_pane)  # the rail eats the width
        self.assertTrue(LayoutState(1280, 800).two_pane)
        self.assertFalse(LayoutState(960, 411, "android").two_pane)

    def test_secondary_destinations_need_room_in_the_rail(self):
        self.assertFalse(LayoutState(841, 673, "android").rail_fits(9))
        self.assertTrue(LayoutState(841, 673, "android").rail_fits(5))
        self.assertTrue(LayoutState(800, 1280, "android").rail_fits(9))
        self.assertTrue(LayoutState(1280, 800).rail_fits(9))
        self.assertTrue(LayoutState(1440, 900).rail_fits(9))
        self.assertFalse(LayoutState(400, 800, "android").rail_fits(5))  # bottom bar

    def test_aspect(self):
        self.assertAlmostEqual(LayoutState(411, 960).aspect, 960 / 411)


class GridTests(unittest.TestCase):
    def test_columns_stay_in_range_for_every_checklist_width(self):
        text = layout.caption_height(title_line=20, meta_line=16, padding=8)
        for (w, h, platform) in CHECKLIST:
            state = LayoutState(w, h, platform)
            avail = state.content_width - 2 * state.page_padding
            metrics = grid_metrics(avail, state.width_class, text_height=text)
            _gap, _min, low, high = layout._GRID[state.width_class]
            with self.subTest(size=(w, h)):
                self.assertGreaterEqual(metrics.columns, low)
                self.assertLessEqual(metrics.columns, high)
                total = metrics.columns * metrics.tile_width + (metrics.columns - 1) * metrics.gap
                self.assertAlmostEqual(total, avail, places=3)  # no overflow, no gap at the end
                self.assertGreater(metrics.child_aspect_ratio, 0.8)

    def test_narrow_phone_keeps_two_columns_with_small_gaps(self):
        metrics = grid_metrics(320 - 32, "compact", text_height=60)
        self.assertEqual((metrics.columns, metrics.gap), (2, 8))

    def test_large_density_on_a_phone_is_one_card_per_row(self):
        self.assertEqual(grid_metrics(380, "compact", text_height=60, density="large").columns, 1)

    def test_cover_slider_overrides_density_and_is_clamped(self):
        small = grid_metrics(1400, "large", text_height=60, cover_size=100)
        big = grid_metrics(1400, "large", text_height=60, cover_size=1000)
        self.assertEqual(small.columns, grid_metrics(1400, "large", text_height=60, cover_size=140).columns)
        self.assertEqual(big.columns, grid_metrics(1400, "large", text_height=60, cover_size=440).columns)
        self.assertEqual(big.columns, 3)  # the slider decides, not the size class's minimum


if __name__ == "__main__":
    unittest.main()
