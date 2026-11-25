"""The subscription store against a fake manager (no window, no network)."""

from __future__ import annotations

import asyncio
import importlib.util
import unittest
from types import SimpleNamespace
from unittest import mock

HAS_FLET = importlib.util.find_spec("flet") is not None


def items(source_id: int) -> list[dict]:
    return [
        {"video_id": f"s{source_id}v1", "title": "Alpha", "is_new": 1, "published_at": "2026-01-03"},
        {"video_id": f"s{source_id}v2", "title": "Beta", "downloaded": 1, "download_file_exists": 1, "published_at": "2026-01-02"},
        {"video_id": f"s{source_id}v3", "title": "Gamma", "published_at": "2026-01-01"},
    ]


class FakeManager:
    def __init__(self):
        self.calls: list[tuple] = []
        self.sources = [
            {"id": 1, "title": "Ann", "source_key": "ann", "source_type": "author", "enabled": 1, "new_count": 1},
            {"id": 2, "title": "Bob list", "source_key": "abc", "source_type": "playlist", "enabled": 0},
        ]

    def get_subscription_sources(self):
        return list(self.sources)

    def get_subscription_recent_items(self, limit, resolve_thumbnails=False):
        return {s["id"]: items(s["id"])[:limit] for s in self.sources}

    def get_subscription_items(self, source_id):
        return items(source_id)

    def set_subscription_enabled(self, source_id, enabled):
        self.calls.append(("enabled", source_id, enabled))

    def remove_subscription_sources(self, ids):
        self.calls.append(("remove", tuple(ids)))
        self.sources = [s for s in self.sources if s["id"] not in ids]

    def mark_subscription_items_seen(self, ids):
        self.calls.append(("seen", tuple(ids)))

    def preview_subscription_items(self, ids, rule_id="", label=""):
        self.calls.append(("preview", tuple(ids), rule_id, label))
        return {"staged": len(ids)}


class _Page:
    def run_task(self, fn, *args, **kwargs):
        return asyncio.ensure_future(fn(*args, **kwargs))


@unittest.skipUnless(HAS_FLET, "Flet is not installed in this environment")
class SubscriptionStoreTests(unittest.TestCase):
    def make(self):
        from app.ui_flet.stores.subscriptions import SubscriptionStore

        staged = []

        async def stage_job(job, **_kwargs):
            staged.append(job())
            return staged[-1]

        downloads = SimpleNamespace(stage_job=stage_job, page_rule=lambda scope: f"rule-{scope}")
        shell = SimpleNamespace(page=_Page(), downloads=downloads)
        manager = FakeManager()
        return SubscriptionStore(shell, manager), manager, staged

    def test_load_builds_sources_and_shelves(self):
        store, _manager, _staged = self.make()
        asyncio.run(store.load())
        self.assertTrue(store.state.loaded)
        self.assertEqual([s["id"] for s in store.visible_sources()], [1, 2])
        self.assertEqual(len(store.recent[1]), 3)
        self.assertEqual(store.state.selected_source, 1)

    def test_query_and_sort(self):
        store, _manager, _staged = self.make()
        asyncio.run(store.load())
        store.set_query("bob")
        self.assertEqual([s["id"] for s in store.visible_sources()], [2])
        store.set_query("")
        store.set_sort("new_count")
        self.assertEqual(store.visible_sources()[0]["id"], 1)

    def test_items_filter_select_and_download(self):
        store, manager, staged = self.make()

        async def run():
            await store.load()
            model = store.items_for(1)
            await model.load()
            self.assertEqual(len(model.cards), 3)
            self.assertEqual(model.states[model.cards[1].key], "downloaded")
            # Downloaded works are not queued again.
            self.assertEqual(model.download_ids(), ["s1v1", "s1v3"])
            self.assertEqual(model.download_ids(only_new=True), ["s1v1"])
            model.set_query("gam")
            self.assertEqual([c.title for c in model.cards], ["Gamma"])
            model.set_query("")
            model.toggle(model.cards[2])
            self.assertEqual(model.selected_ids(), ["s1v3"])
            with mock.patch("app.ui_flet.services.notify.notice"):
                await store.download(model)
            self.assertEqual(manager.calls[-1], ("preview", ("s1v3",), "rule-subscription", "Ann"))
            self.assertEqual(model.state.selected, frozenset())
            self.assertEqual(staged, [{"staged": 1}])

        asyncio.run(run())

    def test_remove_and_pause(self):
        store, manager, _staged = self.make()

        async def run():
            await store.load()
            with mock.patch("app.ui_flet.services.notify.notice"):
                await store.remove([2])
            await store.set_enabled(1, False)

        asyncio.run(run())
        self.assertIn(("remove", (2,)), manager.calls)
        self.assertIn(("enabled", 1, False), manager.calls)
        self.assertEqual([s["id"] for s in store.sources], [1])


if __name__ == "__main__":
    unittest.main()
