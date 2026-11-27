"""Home rows against a fake ``load_feed_page`` and an in-memory cache (no window, no network)."""

from __future__ import annotations

import asyncio
import importlib.util
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from app.config import app_config
from app.core.home.cache import HomeFeedCache
from app.core.home.loading import FeedResult
from app.core.search.models import SearchVideo

HAS_FLET = importlib.util.find_spec("flet") is not None


def rows(prefix: str, count: int) -> list[dict]:
    return [{"id": f"{prefix}{i}", "title": f"{prefix} {i}", "file": {"id": f"f{i}"}} for i in range(count)]


def feed_result(token: int, prefix: str = "v", count: int = 3, error: str = "") -> FeedResult:
    data = rows(prefix, count)
    items = [SearchVideo(video_id=row["id"], title=row["title"]) for row in data]
    return FeedResult(token=token, items=items, rows=data, error=error)


class _Page:
    def __init__(self):
        self.tasks = []

    def run_task(self, fn, *args):
        self.tasks.append(asyncio.ensure_future(fn(*args)))


@unittest.skipUnless(HAS_FLET, "Flet is not installed in this environment")
class HomeStoreTests(unittest.TestCase):
    def setUp(self):
        self.ui: dict[str, object] = {}
        tmp = tempfile.mkdtemp()
        self.cache = HomeFeedCache(os.path.join(tmp, "home_cache.json"))
        patches = [
            mock.patch.object(app_config, "get_ui_value", lambda key, default=None: self.ui.get(key, default)),
            mock.patch.object(app_config, "set_ui_value", lambda key, value: self.ui.__setitem__(key, value)),
            mock.patch("app.ui.stores.home.shared_cache", lambda: self.cache),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def make(self, *, logged_in: bool = False):
        from app.ui.stores.home import HomeStore

        shell = SimpleNamespace(page=_Page(), state=SimpleNamespace(logged_in=logged_in))
        return HomeStore(shell, manager=object())

    def test_rows_follow_the_saved_layout(self):
        store = self.make()
        self.assertEqual([row.section.id for row in store.rows], ["subscriptions", "hot_videos", "hot_images"])

    def test_account_row_asks_to_sign_in(self):
        store = self.make(logged_in=False)
        row = store.row("subscriptions")
        asyncio.run(row.reload())
        self.assertTrue(row.state.login_required)
        self.assertTrue(row.state.message)
        self.assertEqual(row.state.cards, [])

    def test_cache_miss_fetches_and_shows_cards(self):
        store = self.make()
        row = store.row("hot_videos")

        def fake(manager, kind, params, page, limit, **kwargs):
            return feed_result(kwargs["token"])

        with mock.patch("app.ui.stores.home.load_feed_page", side_effect=fake) as loader:
            asyncio.run(row.reload())
        loader.assert_called_once()
        self.assertEqual([card.key for card in row.state.cards], ["video:v0", "video:v1", "video:v2"])
        self.assertFalse(row.state.loading)
        self.assertEqual(row.state.message, "")
        self.assertIsNotNone(self.cache.get(row.feed.cache_key()))

    def test_fresh_cache_paints_without_the_network(self):
        store = self.make()
        row = store.row("hot_videos")
        self.cache.put(row.feed.cache_key(), rows("c", 2))
        with mock.patch("app.ui.stores.home.load_feed_page") as loader:
            asyncio.run(row.reload())
        loader.assert_not_called()
        self.assertEqual(len(row.state.cards), 2)

    def test_failure_without_cards_offers_retry(self):
        store = self.make()
        row = store.row("hot_videos")

        def fake(manager, kind, params, page, limit, **kwargs):
            return FeedResult(token=kwargs["token"], error="boom")

        with mock.patch("app.ui.stores.home.load_feed_page", side_effect=fake):
            asyncio.run(row.reload())
        self.assertTrue(row.state.retry)
        self.assertIn("boom", row.state.message)

    def test_new_posts_wait_while_cards_are_selected(self):
        store = self.make()
        row = store.row("hot_videos")
        with mock.patch("app.ui.stores.home.load_feed_page", side_effect=lambda *a, **k: feed_result(k["token"])):
            asyncio.run(row.reload())
        row.toggle(row.state.cards[0])
        with mock.patch("app.ui.stores.home.load_feed_page",
                        side_effect=lambda *a, **k: feed_result(k["token"], prefix="n")):
            asyncio.run(row.reload(force=True))
        self.assertTrue(row.state.pending)
        self.assertEqual(row.state.cards[0].key, "video:v0")
        row.clear_selection()
        self.assertFalse(row.state.pending)
        self.assertEqual(row.state.cards[0].key, "video:n0")

    def test_folding_is_remembered(self):
        store = self.make()
        row = store.row("hot_images")
        row.set_folded(True)
        self.assertTrue(row.state.collapsed)
        self.assertTrue(self.make().row("hot_images").state.collapsed)

    def test_queueable_uses_selection_first(self):
        store = self.make()
        row = store.row("hot_videos")
        with mock.patch("app.ui.stores.home.load_feed_page", side_effect=lambda *a, **k: feed_result(k["token"])):
            asyncio.run(row.reload())
        self.assertEqual(len(row.queueable()), 3)
        row.toggle(row.state.cards[1])
        self.assertEqual([video.video_id for video in row.queueable()], ["v1"])

    def test_another_account_never_sees_the_old_subscriptions(self):
        store = self.make(logged_in=True)
        row = store.row("subscriptions")
        calls = []

        def fake(manager, kind, params, page, limit, **kwargs):
            calls.append(kind)
            return feed_result(kwargs["token"], prefix="a" if len(calls) == 1 else "b")

        async def scenario():
            with mock.patch("app.ui.stores.home.load_feed_page", side_effect=fake), \
                    mock.patch.object(type(app_config), "username", "alice"):
                store.on_login_changed()  # start-up restores alice
                await asyncio.gather(*store.shell.page.tasks)
                self.assertEqual(row.state.cards[0].key, "video:a0")
                store.on_login_changed()  # alice again (restore): the fresh cache is reused
                await asyncio.gather(*store.shell.page.tasks)
                self.assertEqual(len(calls), 1)
            with mock.patch("app.ui.stores.home.load_feed_page", side_effect=fake), \
                    mock.patch.object(type(app_config), "username", "bob"):
                store.on_login_changed()  # bob signs in
                self.assertEqual(row.state.cards, [])
                await asyncio.gather(*store.shell.page.tasks)

        asyncio.run(scenario())
        self.assertEqual(len(calls), 2)
        self.assertEqual([card.key for card in row.state.cards], ["video:b0", "video:b1", "video:b2"])

    def test_a_fetch_from_before_sign_out_is_dropped(self):
        store = self.make(logged_in=True)
        row = store.row("subscriptions")
        token, _tab, _params = row.feed.begin_fetch()

        async def scenario():
            store.shell.state.logged_in = False
            store.on_login_changed()
            await asyncio.gather(*store.shell.page.tasks)
            row._apply(feed_result(token, prefix="old"))  # the request alice started before signing out

        asyncio.run(scenario())
        self.assertEqual(row.state.cards, [])
        self.assertTrue(row.state.login_required)



class CacheOwnerTests(unittest.TestCase):
    def test_account_entries_follow_their_login(self):
        from app.core.home.cache import OWNER_KEY, owner_id

        cache = HomeFeedCache(os.path.join(tempfile.mkdtemp(), "home_cache.json"))
        cache.put("acct:subscriptions|videos|all", rows("a", 2))
        cache.put("hot_videos|trending|all", rows("h", 2))
        self.assertTrue(cache.claim_account(owner_id("Alice@example.com")))  # unknown owner: not trusted
        cache.put("acct:subscriptions|videos|all", rows("a", 2))
        self.assertFalse(cache.claim_account(owner_id("alice@example.com")))  # same login
        self.assertIsNotNone(cache.get("acct:subscriptions|videos|all"))
        self.assertTrue(cache.claim_account(owner_id("bob")))
        self.assertIsNone(cache.get("acct:subscriptions|videos|all"))
        self.assertIsNotNone(cache.get("hot_videos|trending|all"))
        stored = HomeFeedCache(cache._resolve_path())._load()[OWNER_KEY]["owner"]
        self.assertNotIn("bob", stored)  # a hash, never the login itself
        cache.put("acct:subscriptions|videos|all", rows("b", 1))
        self.assertEqual(cache.clear(), 2)  # Settings → clear: rows only, the owner stays
        self.assertFalse(cache.claim_account(owner_id("bob")))


if __name__ == "__main__":
    unittest.main()
