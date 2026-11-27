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

        self.watched: list[tuple[list[str], bool]] = []

    def get_history_records(self):
        return [dict(r) for r in self.rows]

    def get_history_tags(self):
        return {"v1": ["mmd", "Dance"], "v3": ["dance"]}

    def set_videos_watched(self, ids, watched=True):
        self.watched.append((list(ids), watched))
        return len(ids)

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
        from app.ui.stores.library import LibraryStore

        ui = {}
        patches = [
            mock.patch("app.ui.stores.library.app_config.get_ui_value", lambda key, default=None: ui.get(key, default)),
            mock.patch("app.ui.stores.library.app_config.set_ui_value", lambda key, value: ui.__setitem__(key, value)),
            mock.patch("app.ui.services.notify.notice"),
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


@unittest.skipUnless(HAS_FLET, "Flet is not installed in this environment")
class LibraryBrowsingTests(unittest.TestCase):
    make = LibraryStoreTests.make

    def test_tags_filter_case_insensitively(self):
        store, _manager, _ui = self.make()
        asyncio.run(store.load())
        self.assertEqual(store.tag_list[0], ("Dance", 2))
        store.toggle_tag("DANCE")
        self.assertEqual(sorted(r["video_id"] for r in store.visible), ["v1", "v3"])
        store.toggle_tag("dance")  # the same tag, any spelling, turns it off
        self.assertEqual(len(store.visible), 5)

    def test_neighbours_follow_the_list_and_playing_marks_watched(self):
        async def run():
            store, manager, _ui = self.make()
            await store.load()
            ids = [r["video_id"] for r in store.visible]
            self.assertEqual(store.neighbour(ids[0], 1), ids[1])
            self.assertEqual(store.neighbour(ids[0], -1), "")
            self.assertEqual(store.neighbour(ids[-1], 1), "")
            store.play(ids[0])
            await asyncio.sleep(0.05)
            self.assertEqual(store.state.playing, ids[0])
            self.assertEqual(manager.watched, [([ids[0]], True)])

        asyncio.run(run())

    def test_next_still_found_after_playing_drops_the_record_from_the_list(self):
        async def run():
            store, _manager, _ui = self.make()
            await store.load()
            ids = [r["video_id"] for r in store.visible]
            store.play(ids[1])
            # The "unwatched" scope drops the record once it is marked watched.
            store.visible = [r for r in store.visible if r["video_id"] != ids[1]]
            self.assertEqual(store.neighbour(ids[1], 1), ids[2])
            self.assertEqual(store.neighbour(ids[1], -1), ids[0])

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
