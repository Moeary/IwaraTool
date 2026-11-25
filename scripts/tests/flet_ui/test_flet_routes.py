"""Shell destinations and route parsing."""

from __future__ import annotations

import unittest

from app.ui_flet import routes


class RouteTests(unittest.TestCase):
    def test_parse_and_build(self):
        self.assertEqual(routes.parse("/library/123?x=1"), routes.Route("library", ("123",)))
        self.assertEqual(routes.parse("/nowhere"), routes.Route(routes.HOME))
        self.assertEqual(routes.parse(""), routes.Route(routes.HOME))
        self.assertEqual(routes.route(routes.DETAIL, "video", "abc"), "/detail/video/abc")
        self.assertTrue(routes.parse("/search").is_root)

    def test_startup_values_of_the_old_interface(self):
        self.assertEqual(routes.startup_destination("download"), routes.DOWNLOADS)
        self.assertEqual(routes.startup_destination("history"), routes.LIBRARY)
        self.assertEqual(routes.startup_destination("bogus"), routes.HOME)
        self.assertEqual(routes.startup_destination("repair", desktop=False), routes.HOME)

    def test_desktop_only_destinations_are_hidden_on_phones(self):
        self.assertNotIn(routes.TOOLS, routes.available(routes.SECONDARY, desktop=False))
        self.assertIn(routes.TOOLS, routes.available(routes.SECONDARY, desktop=True))

    def test_every_destination_has_a_label(self):
        for destination in (*routes.DESTINATIONS, routes.DETAIL, routes.AUTHOR):
            self.assertNotEqual(routes.label(destination), destination)

    def test_bar_labels_are_short(self):
        from app.i18n import pinned_language

        for language in ("en", "zh", "ja"):
            with pinned_language(language):
                for destination in routes.PRIMARY:
                    self.assertLessEqual(len(routes.short_label(destination)), 8 if language == "en" else 5, destination)


if __name__ == "__main__":
    unittest.main()
