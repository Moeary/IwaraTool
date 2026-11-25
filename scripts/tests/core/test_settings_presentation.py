"""Settings rules shared by both interfaces (no Qt, no Flet)."""

from __future__ import annotations

import json
import unittest
from unittest import mock

from app.config import app_config
from app.core.home.feed import MODE_BROWSE, MODE_KEYWORD, MODE_SUBSCRIPTIONS, MODE_TAGS, HomeSectionSpec
from app.presentation import shortcuts
from app.presentation.home import layout as home_layout
from app.presentation.settings import catalog, values
from app.presentation.settings.texts import TEXTS, stext


class SettingsValuesTests(unittest.TestCase):
    def setUp(self):
        self.ui: dict = {}
        patches = [
            mock.patch.object(app_config, "get_ui_value", lambda key, default=None: self.ui.get(key, default)),
            mock.patch.object(app_config, "set_ui_value", lambda key, value: self.ui.__setitem__(key, value)),
            mock.patch.object(values, "_bus", return_value=mock.Mock()),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_parse_int_clamps_and_keeps_current_on_junk(self):
        self.assertEqual(values.parse_int("50", current=3, low=1, high=10), 10)
        self.assertEqual(values.parse_int("abc", current=3, low=1, high=10), 3)
        self.assertEqual(values.parse_int("", current=3, low=0, high=10, empty=7), 7)
        self.assertEqual(values.parse_int(" 0 ", current=3, low=1, high=10), 1)

    def test_lower_history_limit_trims_saved_history(self):
        self.ui[values.SEARCH_HISTORY_KEY] = json.dumps([{"q": i} for i in range(10)])
        with mock.patch.object(type(app_config), "search_history_limit", new_callable=mock.PropertyMock) as limit:
            limit.return_value = 20
            self.assertEqual(values.set_search_history_limit(3), 3)
        self.assertEqual(len(json.loads(self.ui[values.SEARCH_HISTORY_KEY])), 3)

    def test_workers_and_modes_are_clamped(self):
        self.assertEqual(values.set_resolution_workers(99), 8)
        self.assertEqual(values.resolution_workers(), 8)
        self.assertEqual(values.set_resolution_mode("weird"), "eager")
        self.assertEqual(values.set_cover_workers(0), 1)
        self.ui[values.INCREMENTAL_KEY] = "off"
        self.assertFalse(values.incremental_refresh())

    def test_reports(self):
        ok, _title, content, log = values.temp_cleanup_report(3, 1)
        self.assertFalse(ok)
        self.assertIn("3", content)
        self.assertIn("1", log)
        ok, *_ = values.aria2_test_report(None, "refused")
        self.assertFalse(ok)
        ok, line, _title, _log = values.tag_dictionary_result((12, ""))
        self.assertTrue(ok)
        self.assertIn("12", line)
        self.assertEqual(values.player_command_for("/opt/my player"), '"/opt/my player" "{file}"')


class CatalogTests(unittest.TestCase):
    def test_categories_have_texts_and_aliases(self):
        for category in catalog.CATEGORIES:
            self.assertIn(category.title_key, TEXTS)
            self.assertIn(category.summary_key, TEXTS)
            self.assertTrue(category.title)
        self.assertEqual(catalog.category("account").key, "network")
        self.assertIsNone(catalog.category("nope"))
        self.assertTrue(stext("settings"))


class HomeLayoutTests(unittest.TestCase):
    def test_editor_rules(self):
        self.assertTrue(home_layout.needs_value(MODE_TAGS))
        self.assertFalse(home_layout.shows_sort(MODE_SUBSCRIPTIONS))
        self.assertEqual(home_layout.pick_sort(MODE_BROWSE, "nope"), "")
        self.assertEqual(home_layout.pick_sort(MODE_KEYWORD, ""), "relevance")
        self.assertIsNone(home_layout.build_spec(None, mode=MODE_TAGS, content="video", title="", value="", sort="date"))
        spec = home_layout.build_spec(None, mode=MODE_TAGS, content="video", title="", value="hmv", sort="date")
        self.assertEqual(spec.value, "hmv")

    def test_changed_builtin_row_gets_a_new_id(self):
        original = HomeSectionSpec(id="hot_videos", mode=MODE_BROWSE, content="video")
        same = home_layout.build_spec(original, mode=MODE_BROWSE, content="video", title="x", value="", sort="")
        self.assertEqual(same.id, "hot_videos")
        changed = home_layout.build_spec(original, mode=MODE_BROWSE, content="image", title="", value="", sort="")
        self.assertNotEqual(changed.id, "hot_videos")

    def test_move(self):
        specs = [HomeSectionSpec(id=str(i)) for i in range(3)]
        self.assertEqual([s.id for s in home_layout.move(specs, 0, 1)], ["1", "0", "2"])
        self.assertEqual([s.id for s in home_layout.move(specs, 0, -1)], ["0", "1", "2"])


class ShortcutTextTests(unittest.TestCase):
    def test_native_text(self):
        self.assertEqual(shortcuts.native_text("Ctrl+Shift+K", mac=True), "⇧⌘K")
        self.assertEqual(shortcuts.native_text("Ctrl+,", mac=True), "⌘,")
        self.assertEqual(shortcuts.native_text("Ctrl++", mac=True), "⌘+")
        self.assertEqual(shortcuts.native_text("Ctrl+K, Ctrl+S", mac=False), "Ctrl+K, Ctrl+S")
        self.assertEqual(shortcuts.native_text(""), "")


if __name__ == "__main__":
    unittest.main()
