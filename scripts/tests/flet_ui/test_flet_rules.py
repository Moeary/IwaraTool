"""The rules store and form parsing against a temporary rule file (no window)."""

from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

HAS_FLET = importlib.util.find_spec("flet") is not None


class RuleFormTests(unittest.TestCase):
    def test_round_trip_and_validation(self):
        from app.presentation import rule_editing

        form = rule_editing.form_from_payload({"filter_min_likes": 5, "filename_template": "{title}.mp4"})
        self.assertEqual(form["filter_min_likes"], "5")
        payload = rule_editing.payload_from_form(form)
        self.assertEqual(payload["filter_min_likes"], 5)
        with self.assertRaises(ValueError):
            rule_editing.payload_from_form({**form, "filter_start_date": "2026-13-40"})
        with self.assertRaises(ValueError):
            rule_editing.payload_from_form({**form, "filename_template": "{nope}.mp4"})
        self.assertIn("Sample Title", rule_editing.template_preview_text("{title}.mp4"))


@unittest.skipUnless(HAS_FLET, "Flet is not installed in this environment")
class RulesStoreTests(unittest.TestCase):
    def setUp(self):
        from app.core import rules as core_rules

        self.store_file = os.path.join(tempfile.mkdtemp(), "rules.json")
        temp_store = core_rules.RuleStore(self.store_file)
        ui: dict = {}
        patches = [
            mock.patch.object(core_rules, "rule_store", temp_store),
            mock.patch("app.presentation.rule_editing.rule_store", temp_store),
            mock.patch.object(core_rules.app_config, "get_ui_value", lambda key, default=None: ui.get(key, default)),
            mock.patch.object(core_rules.app_config, "set_ui_value", lambda key, value: ui.__setitem__(key, value)),
            mock.patch("app.presentation.rule_editing.apply_rule_payload", lambda payload: payload),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_new_save_default_and_delete(self):
        from app.core.rules import BUILTIN_DEFAULT_RULE_ID
        from app.presentation.rules import DRAFT_RULE_ID
        from app.ui_flet.stores.rules import RulesStore

        store = RulesStore(SimpleNamespace())
        store.load()
        self.assertEqual(store.state.selected, store.active_id)
        store.new()
        self.assertEqual(store.state.selected, DRAFT_RULE_ID)
        self.assertTrue(store.state.dirty)
        store.set_name("Mine")
        store.set_field("download_video_file", False)
        store.set_field("mark_submitted_as_downloaded", True)
        store.set_field("filename_template", "{id}.mp4")
        rule = store.save()
        self.assertIsNotNone(rule)
        self.assertFalse(store.state.dirty)
        self.assertTrue(store.rule(rule["id"])["payload"]["mark_submitted_as_downloaded"])
        self.assertTrue(store.make_default())
        self.assertEqual(store.active_id, rule["id"])
        self.assertTrue(store.delete())
        self.assertEqual(store.active_id, BUILTIN_DEFAULT_RULE_ID)

    def test_invalid_template_is_reported(self):
        from app.ui_flet.stores.rules import RulesStore

        store = RulesStore(SimpleNamespace())
        store.load()
        store.new()
        store.set_field("filename_template", "{bad")
        self.assertIsNone(store.save())
        self.assertTrue(store.state.error)


if __name__ == "__main__":
    unittest.main()
