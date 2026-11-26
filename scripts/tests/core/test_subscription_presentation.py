"""Subscription and author presentation helpers (no UI framework)."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.presentation.author import status as author_status
from app.presentation.author.page import author_section
from app.presentation.subscription import text
from app.presentation.subscription.source_actions import add_source_from_text, remove_sources


class SubscriptionTextTests(unittest.TestCase):
    def test_choices_cover_the_query_modes(self):
        from app.presentation.subscription.item_queries import ITEM_SORTS, STATE_FILTERS

        self.assertEqual([k for k, _ in text.state_filter_choices()], list(STATE_FILTERS))
        self.assertEqual([k for k, _ in text.item_sort_choices()], list(ITEM_SORTS))
        self.assertEqual(len(text.source_sort_choices()), len(text.SOURCE_SORT_OPTIONS))

    def test_source_lines(self):
        source = {"title": "Ann", "source_key": "ann", "source_type": "author", "enabled": 0,
                  "item_count": 5, "undownloaded_count": 2, "last_checked_at": "2026-01-02T03:04:05"}
        self.assertTrue(text.source_subtitle(source).startswith("@ann"))
        self.assertIn(text.paused_text(), text.source_subtitle(source))
        self.assertIn("2026-01-02", text.source_stats_text(source))
        self.assertIn("5", text.source_stats_text(source))

    def test_refresh_progress_and_result(self):
        completed, total, line = text.refresh_progress({"stage": "started", "index": 2, "total": 4, "title": "Ann"})
        self.assertEqual((completed, total), (1, 4))
        self.assertIn("Ann", line)
        self.assertEqual(text.refresh_finished({"errors": [{"error": "boom"}]})[0], "warning")
        self.assertEqual(text.refresh_finished({"new": 3})[0], "success")

    def test_delete_confirmation_names(self):
        sources = [{"id": 1, "title": "Ann"}, {"id": 2, "source_key": "bob"}]
        question, summary = text.delete_confirmation(sources, [1])
        self.assertEqual(summary, "Ann")
        _question, summary = text.delete_confirmation(sources, [1, 2, 3])
        self.assertIn("bob", summary)
        self.assertIn("#3", summary)


class SourceActionTests(unittest.TestCase):
    def manager(self):
        calls = []
        return calls, SimpleNamespace(
            add_author_subscription=lambda key: calls.append(("author", key)) or 7,
            add_playlist_subscription=lambda key: calls.append(("playlist", key)) or 8,
            remove_subscription_sources=lambda ids: calls.append(("remove", tuple(ids))),
        )

    def test_add_author_and_playlist(self):
        calls, manager = self.manager()
        self.assertEqual(add_source_from_text(manager, "https://www.iwara.tv/profile/ann"), 7)
        self.assertEqual(add_source_from_text(manager, "https://www.iwara.tv/playlist/abc-def"), 8)
        self.assertEqual(calls[0], ("author", "ann"))
        self.assertEqual(calls[1][0], "playlist")

    def test_bad_input(self):
        _calls, manager = self.manager()
        self.assertIsNone(add_source_from_text(manager, "   "))

    def test_remove(self):
        calls, manager = self.manager()
        remove_sources(manager, [1, 2])
        self.assertEqual(calls, [("remove", (1, 2))])


class AuthorStatusTests(unittest.TestCase):
    def test_profile_follow(self):
        user, error = author_status.profile_follow(({"user": {"id": "u", "following": True}}, ""))
        self.assertTrue(user["following"])
        self.assertEqual(author_status.profile_follow((None, "nope")), (None, "nope"))
        self.assertEqual(author_status.profile_follow(None, "boom"), (None, "boom"))

    def test_chips(self):
        self.assertEqual(author_status.web_chip_state("ready", True)[1], "accent")
        self.assertEqual(author_status.local_chip_state({"enabled": 0})[1], "warning")
        self.assertEqual(author_status.clean_username(" @ann/ "), "ann")

    def test_author_section_tabs(self):
        section = author_section("ann", "Ann")
        self.assertEqual([tab.id for tab in section.tabs], ["videos", "images"])
        self.assertTrue(all(tab.value == "ann" for tab in section.tabs))


if __name__ == "__main__":
    unittest.main()
