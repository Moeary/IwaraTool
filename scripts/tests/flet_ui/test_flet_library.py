"""The library store against a fake manager (no window)."""

from __future__ import annotations

import asyncio
import importlib.util
import unittest
from types import SimpleNamespace
from unittest import mock

HAS_FLET = importlib.util.find_spec("flet") is not None


class FakeManager:
    def __init__(self):
        self.removed: list[list[str]] = []
        self.rows = [
            {"video_id": f"v{i}", "title": f"T{i}", "author": "ann" if i % 2 else "bob", "likes": i,
             "downloaded_at": f"2026-11-{10 + i:02d} 10:00:00", "file_path": ""}
            for i in range(5)
        ]

    def get_history_records(self):
        return [dict(r) for r in self.rows]

    def remove_history_records(self, ids):
        self.removed.append(list(ids))
        self.rows = [r for r in self.rows if r["video_id"] not in ids]
        return len(ids)


class _Page:
    def run_task(self, fn, *args, **kwargs):
        return asyncio.ensure_future(fn(*args, **kwargs))


@unittest.skipUnless(HAS_FLET, "Flet is not installed in this environment")
class LibraryStoreTests(unittest.TestCase):
    def make(self):
        from app.ui_flet.stores.library import LibraryStore

        ui = {}
        patches = [
            mock.patch("app.ui_flet.stores.library.app_config.get_ui_value", lambda key, default=None: ui.get(key, default)),
            mock.patch("app.ui_flet.stores.library.app_config.set_ui_value", lambda key, value: ui.__setitem__(key, value)),
            mock.patch("app.ui_flet.services.notify.notice"),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        manager = FakeManager()
        return LibraryStore(SimpleNamespace(page=_Page()), manager), manager, ui

    def test_load_sort_and_filter(self):
        store, _manager, ui = self.make()
        asyncio.run(store.load())
        self.assertEqual([r["video_id"] for r in store.visible][:2], ["v4", "v3"])  # newest download first
        store.set_sort("title")
        self.assertEqual(store.visible[0]["video_id"], "v0")
        store.set_sort("title")
        self.assertEqual(store.visible[0]["video_id"], "v4")
        store.set_scope("author")
        store.set_author("ann")
        self.assertEqual({r["author"] for r in store.visible}, {"ann"})
        self.assertEqual(ui["history_scope_v1"], "author")
        self.assertEqual(dict(store.authors)["bob"], 3)

    def test_remove_can_be_undone(self):
        store, manager, _ui = self.make()

        async def run():
            await store.load()
            store.remove(["v1", "v2"])
            self.assertNotIn("v1", store.by_id)
            self.assertEqual(manager.removed, [])
            store.undo()
            self.assertIn("v1", store.by_id)
            store.remove(["v3"])
            store.flush()

        asyncio.run(run())
        self.assertEqual(manager.removed, [["v3"]])
        self.assertNotIn("v3", store.by_id)

    def test_selection_and_paging(self):
        store, _manager, _ui = self.make()
        asyncio.run(store.load())
        store.select_all()
        self.assertEqual(len(store.state.selected), 5)
        store.toggle("v0")
        self.assertNotIn("v0", store.state.selected)
        self.assertEqual(len(store.shown()), 5)


if __name__ == "__main__":
    unittest.main()
