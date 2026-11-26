"""The one-time offer to import the Classic build's data (ui_flet/services/classic_import.py)."""
from __future__ import annotations

import asyncio
import importlib.util
import unittest
from types import SimpleNamespace
from unittest import mock

HAS_FLET = importlib.util.find_spec("flet") is not None


class _Config:
    app_data_dir = "/flet-data"

    def __init__(self):
        self.ui = {}

    def get_ui_value(self, key, default=""):
        return self.ui.get(key, default)

    def set_ui_value(self, key, value, *, sync=True):
        self.ui[key] = value


@unittest.skipUnless(HAS_FLET, "Flet is not installed in this environment")
class OfferTests(unittest.TestCase):
    def _offer(self, config, *, found="/classic", pending=False):
        from app.ui_flet.services import classic_import as service

        shown = []
        with mock.patch.object(service, "app_config", config), \
                mock.patch.object(service.classic_import, "classic_data_dir", return_value=found), \
                mock.patch.object(service.backup, "has_pending_restore", return_value=pending), \
                mock.patch.object(service, "notice", lambda *a, **k: shown.append(k)):
            asyncio.run(service.offer_classic_import(SimpleNamespace(run_task=lambda *a: None)))
        return shown

    def test_offered_once_when_found(self):
        config = _Config()
        first = self._offer(config)
        self.assertEqual(len(first), 1)
        self.assertTrue(first[0]["action"])
        self.assertEqual(self._offer(config), [])

    def test_not_offered_without_classic_data_or_with_a_pending_restore(self):
        self.assertEqual(self._offer(_Config(), found=""), [])
        config = _Config()
        self.assertEqual(self._offer(config, pending=True), [])
        self.assertNotIn("classic_import_offered", config.ui)  # still offered later


if __name__ == "__main__":
    unittest.main()
