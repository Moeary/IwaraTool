"""Shell navigation: per-destination stacks, links across destinations, routes over a destination."""

from __future__ import annotations

import unittest

from app.ui_flet import routes
from app.ui_flet.navigation import ShellNavigator


class ShellNavigatorTests(unittest.TestCase):
    def setUp(self):
        self.moves = []
        self.nav = ShellNavigator(lambda dest, path: self.moves.append((dest, path)))

    def test_switching_is_not_a_step_and_keeps_each_destination_where_it_was(self):
        self.nav.open("/detail/video/a")
        self.assertEqual(self.nav.current.name, routes.HOME)  # opened over Home
        self.nav.switch(routes.SEARCH)
        self.nav.switch(routes.HOME)
        self.assertEqual(self.nav.path, "/detail/video/a")
        self.assertTrue(self.nav.back())
        self.assertEqual(self.nav.path, "/home")
        self.assertFalse(self.nav.back())

    def test_link_into_another_destination_returns_to_the_caller(self):
        self.nav.switch(routes.SEARCH)
        self.nav.open("/detail/video/a")
        self.nav.open("/library/42")  # "show in library" from a search result's detail
        self.assertEqual((self.nav.current.name, self.nav.path), (routes.LIBRARY, "/library/42"))
        self.assertTrue(self.nav.back())
        self.assertEqual((self.nav.current.name, self.nav.path), (routes.SEARCH, "/detail/video/a"))
        self.assertTrue(self.nav.back())
        self.assertEqual(self.nav.path, "/search")

    def test_show_changes_the_sub_route_without_a_step(self):
        self.nav.switch(routes.SETTINGS)
        self.nav.show("/settings/about")
        self.assertEqual(self.nav.path, "/settings/about")
        self.nav.show("/settings")
        self.assertFalse(self.nav.can_go_back())
        self.nav.show("/search")  # another destination: ignored
        self.assertEqual(self.nav.path, "/settings")

    def test_deep_route_without_steps_goes_back_to_the_root(self):
        self.nav.destinations[routes.SETTINGS].path = "/settings/network"
        self.nav.switch(routes.SETTINGS)
        self.assertTrue(self.nav.can_go_back())
        self.assertTrue(self.nav.back())
        self.assertEqual(self.nav.path, "/settings")
        self.assertFalse(self.nav.can_go_back())

    def test_reopening_the_same_place_is_ignored_and_reselect_goes_to_top(self):
        self.nav.open("/home")
        self.assertEqual(self.moves, [])
        self.nav.open("/detail/video/a")
        self.nav.go_root(routes.HOME)
        self.assertEqual(self.nav.path, "/home")
        self.assertEqual(self.moves[-1], (routes.HOME, "/home"))

    def test_page_state_travels_with_the_route(self):
        home = self.nav.destinations[routes.HOME]
        scroll = {"offset": 120}
        home.snapshot_state = lambda: dict(scroll)
        home.restore_state = lambda state: scroll.update(state)
        self.nav.open("/detail/video/a")
        scroll["offset"] = 0
        self.nav.back()
        self.assertEqual(scroll["offset"], 120)


if __name__ == "__main__":
    unittest.main()
