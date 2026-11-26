"""The Repair Center store: scan → preview → repair, lookups and option re-planning (no window)."""

from __future__ import annotations

import asyncio
import importlib.util
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

HAS_FLET = importlib.util.find_spec("flet") is not None


@unittest.skipUnless(HAS_FLET, "Flet is not installed in this environment")
class RepairStoreTests(unittest.TestCase):
    def setUp(self):
        from scripts.tests.core.test_repair_runs import FakeManager

        self.manager = FakeManager(3)
        self.ui: dict = {}
        patches = [
            mock.patch("app.presentation.repair.page.app_config.get_ui_value", lambda key, default=None: self.ui.get(key, default)),
            mock.patch("app.presentation.repair.page.app_config.set_ui_value", lambda key, value: self.ui.__setitem__(key, value)),
            mock.patch("app.ui_flet.stores.repair.rule_store.list_available",
                       lambda: [{"id": "r1", "name": "One", "payload": {"filename_template": "{id}.mp4"}}]),
            mock.patch("app.ui_flet.stores.repair.active_rule_id", lambda: "r1"),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def _store(self, loop):
        from app.ui_flet.stores.repair import RepairStore

        page = SimpleNamespace(run_task=lambda fn, *args: loop.create_task(fn(*args)))
        return RepairStore(SimpleNamespace(loop=loop, page=page), self.manager)

    def test_flow(self):
        from app.ui_flet.stores import repair

        async def run():
            store = self._store(asyncio.get_running_loop())
            store.load()
            self.assertEqual(store.state.form.template, "{id}.mp4")
            self.assertFalse(await store.scan())  # no folder yet
            folder = tempfile.mkdtemp()
            store.update(folder=folder)
            self.assertTrue(await store.scan())
            await asyncio.sleep(0)  # pending batches
            self.assertEqual(len(store.items), 3)
            self.assertEqual(store.state.step, repair.STEP_PREVIEW)
            self.assertEqual(self.ui["repair_folder"], folder)
            self.assertEqual(self.manager.calls[0][2], "{id}.mp4")

            # An option change re-plans the targets after the debounce.
            store.update(collect_nfo=False)
            await asyncio.sleep(0.3)
            self.assertTrue(all(i["target_path"].endswith(".new") for i in store.items))

            self.assertFalse(await store.resolve("/v/0.mp4", "bad id!"))
            self.assertTrue(await store.resolve("/v/0.mp4", "abcdefgh"))
            self.assertEqual(store.by_path["/v/0.mp4"]["video_id"], "abcdefgh")

            self.assertTrue(await store.repair())
            self.assertEqual(store.state.step, repair.STEP_DONE)
            self.assertEqual(store.state.result["renamed"], 3)
            self.assertTrue(all(i["status"] == "completed" for i in store.items))
            self.assertFalse(store.manager.calls[-1][2]["collect_nfo"])
            self.assertTrue(store.log)

        asyncio.run(run())

    def test_mode_is_saved_at_once(self):
        from app.presentation.repair import page as rp

        async def run():
            store = self._store(asyncio.get_running_loop())
            store.load()
            store.update(mode=rp.MODE_ORGANIZE)
            self.assertEqual(self.ui["repair_mode"], rp.MODE_ORGANIZE)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
