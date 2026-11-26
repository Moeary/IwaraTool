"""The search store against a fake ``run_search`` (no window, no network)."""

from __future__ import annotations

import asyncio
import importlib.util
import unittest
from types import SimpleNamespace
from unittest import mock

from app.core.search.models import SearchAuthor, SearchPageResult, SearchVideo
from app.presentation.media.cards import KIND_AUTHOR, author_card, video_card

HAS_FLET = importlib.util.find_spec("flet") is not None


def videos(start: int, count: int) -> list[SearchVideo]:
    return [SearchVideo(video_id=f"v{i}", title=f"Video {i}", views=1500, likes=20, duration=125) for i in range(start, start + count)]


class CardTests(unittest.TestCase):
    def test_video_card_text(self):
        card = video_card(SearchVideo(video_id="v1", title="T", author_username="ann", author_name="Ann", views=1500,
                                      likes=20, duration=125, published_at="2026-01-02T03:04:05"))
        self.assertEqual(card.key, "video:v1")
        self.assertEqual(card.duration, "2:05")
        self.assertIn("1.5K", card.meta)
        self.assertIn("2026-01-02", card.meta)
        self.assertEqual(card.subtitle, "Ann @ann")
        self.assertEqual(card.history_id, "v1")
        self.assertTrue(card.selectable)

    def test_authors_are_round_and_not_selectable(self):
        card = author_card(SearchAuthor(author_id="a1", username="ann", video_count=3))
        self.assertEqual(card.kind, KIND_AUTHOR)
        self.assertTrue(card.round_cover)
        self.assertFalse(card.selectable)


class _Page:
    def __init__(self):
        self.tasks = []

    def run_task(self, fn, *args):
        self.tasks.append(asyncio.ensure_future(fn(*args)))


@unittest.skipUnless(HAS_FLET, "Flet is not installed in this environment")
class SearchStoreTests(unittest.TestCase):
    def run_async(self, body):
        return asyncio.run(body())

    def make(self):
        from app.ui_flet.stores.search import SearchStore

        page = _Page()
        manager = SimpleNamespace(history=SimpleNamespace(get_records=lambda ids: {}))
        shell = SimpleNamespace(page=page)
        store = SearchStore(shell, manager)
        store.state.source = "iwara"
        return store, page

    def pages(self, *_args, **kwargs):
        page = _args[2]
        return SearchPageResult(scope="videos", videos=videos(page * 4, 4), current_page=page, next_page=page + 1 if page < 1 else None,
                                total=8)

    def test_search_then_load_more_appends_without_duplicates(self):
        async def body():
            store, page = self.make()
            with mock.patch("app.ui_flet.stores.search.run_search", side_effect=self.pages), \
                 mock.patch("app.ui_flet.stores.search.record_search", return_value=[]):
                await store.search()
                self.assertEqual(len(store.cards), 4)
                self.assertTrue(store.state.has_next)
                await store.load_more()
                await asyncio.gather(*page.tasks, return_exceptions=True)
            return store

        store = self.run_async(body)
        self.assertEqual([card.key for card in store.cards][-1], "video:v7")
        self.assertEqual(len(store.cards), 8)
        self.assertFalse(store.state.has_next)
        self.assertIn("8", store.state.status)

    def test_the_search_can_ask_whether_to_stop(self):
        # The rating scan calls should_stop() between pages; a bool there failed every such search.
        async def body():
            store, _page = self.make()
            answers = []

            def asks(filters, scope, page, *, should_stop, **_kwargs):
                answers.append(should_stop())
                return SearchPageResult(scope="videos", videos=videos(0, 2))

            with mock.patch("app.ui_flet.stores.search.run_search", side_effect=asks), \
                 mock.patch("app.ui_flet.stores.search.record_search", return_value=[]):
                await store.search()
            return store, answers

        store, answers = self.run_async(body)
        self.assertEqual(answers, [False])
        self.assertEqual(len(store.cards), 2)

    def test_a_result_from_a_superseded_search_is_dropped(self):
        async def body():
            store, _page = self.make()
            calls = []

            def slow(filters, scope, page, **_kwargs):
                calls.append(filters.keyword)
                return SearchPageResult(scope="videos", videos=videos(0 if filters.keyword == "old" else 10, 2))

            with mock.patch("app.ui_flet.stores.search.record_search", return_value=[]), \
                 mock.patch("app.ui_flet.stores.search.run_search", side_effect=slow):
                store.state.keyword = "old"
                first = asyncio.ensure_future(store.search())
                await asyncio.sleep(0)
                store.state.keyword = "new"
                await store.search()
                await first
            return store

        store = self.run_async(body)
        self.assertEqual([card.key for card in store.cards], ["video:v10", "video:v11"])

    def test_selection(self):
        store, _page = self.make()
        store.cards = [video_card(v) for v in videos(0, 3)] + [author_card(SearchAuthor(author_id="a", username="a"))]
        store.items = {card.key: object() for card in store.cards}
        store.toggle(store.cards[0])
        self.assertTrue(store.state.selecting)
        store.toggle(store.cards[3])  # authors cannot be queued
        self.assertEqual(store.state.selected, frozenset({"video:v0"}))
        store.select_all()
        self.assertEqual(len(store.state.selected), 3)
        store.clear_selection()
        self.assertFalse(store.state.selecting)

    def test_oreno3d_is_refused_on_iwara_ai(self):
        store, _page = self.make()
        with mock.patch("app.presentation.search.options.oreno3d_available", return_value=False):
            store.set_source("oreno3d")
        self.assertEqual(store.state.source, "iwara")
        store.set_source("iwara")
        store.set_scope("playlists")
        store.set_keyword("x")
        self.assertEqual([k for _l, k in store.sort_choices()], ["date", "relevance"])

    def test_tag_suggestions_complete_the_keyword(self):
        from app.core.tag_dictionary import TagSuggestion

        async def body():
            store, page = self.make()
            calls = []

            def suggest(query, limit=12):
                calls.append(query)
                return [TagSuggestion("dance", "Dance", "舞蹈", "ダンス")]

            store.manager.get_search_tag_suggestions = suggest
            store.set_keyword("dan")  # not the tags scope: nothing
            await asyncio.gather(*page.tasks, return_exceptions=True)
            self.assertEqual(calls, [])
            store.set_scope("tags")
            store.set_keyword("loop, dan")
            await asyncio.gather(*page.tasks, return_exceptions=True)
            self.assertEqual([key for key, _label, _tip in store.state.suggestions], ["dance"])
            store.apply_suggestion("dance")
            self.assertEqual(store.state.suggestions, [])
            return store, calls

        store, calls = self.run_async(body)
        self.assertEqual(calls, ["dan"])
        self.assertIn("dance", store.state.keyword)
        self.assertTrue(store.state.keyword.startswith("loop"))


if __name__ == "__main__":
    unittest.main()
