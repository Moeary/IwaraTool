"""app.presentation without Qt: navigation stacks, clicks, staging notices, queueing, shortcuts, comments."""

from __future__ import annotations

import unittest

from app.core.search.models import SearchPlaylist, SearchVideo
from app.presentation.click_actions import ACTION_DETAIL, ACTION_NONE, ACTION_SELECT, ClickDecider
from app.presentation.downloads.staging import (
    NOTICE_ERROR,
    NOTICE_SUCCESS,
    NOTICE_WARNING,
    stage,
    staging_notice,
    video_payload,
)
from app.presentation.media.comments import comment_view, comments_hint, more_comments_available
from app.presentation.navigation import NavigationController
from app.presentation.search.queueing import queue_payloads, queue_selection
from app.presentation.shortcuts import portable_normalize


class _Page:
    def __init__(self, name):
        self.name = name
        self.state = 0
        self.alive = True

    def nav_snapshot(self):
        return self.state

    def nav_restore(self, state):
        self.state = state


class NavigationTests(unittest.TestCase):
    def setUp(self):
        self.home, self.search = _Page("home"), _Page("search")
        self.front = self.home
        self.nav = NavigationController(
            resolve_page=lambda page: page,
            current_page=lambda: self.front,
            switch_to=self._switch,
            is_alive=lambda page: page.alive,
        )

    def _switch(self, page):
        self.front = page

    def test_each_page_goes_back_through_its_own_steps(self):
        self.home.state = 1
        self.nav.record()
        self.home.state = 2
        self.front = self.search
        self.nav.record(self.home, into=self.search)  # a link from Home opened Search
        self.assertTrue(self.nav.back())
        self.assertIs(self.front, self.home)
        self.assertEqual(self.home.state, 2)
        self.assertTrue(self.nav.back())
        self.assertEqual(self.home.state, 1)
        self.assertFalse(self.nav.back())

    def test_same_place_is_recorded_once_and_quiet_moves_not_at_all(self):
        self.assertTrue(self.nav.record())
        self.assertFalse(self.nav.record())
        with self.nav.quiet():
            self.home.state = 5
            self.assertFalse(self.nav.record())
        self.assertEqual(len(self.nav), 1)

    def test_gone_pages_are_skipped(self):
        self.nav.record(self.search, into=self.home)
        self.search.alive = False
        self.assertFalse(self.nav.back())


class ClickTests(unittest.TestCase):
    def _decider(self, single, double):
        self.done, self.timer = [], []
        return ClickDecider(
            lambda action, target: self.done.append((action, target)),
            start_timer=lambda: self.timer.append("start"),
            stop_timer=lambda: self.timer.append("stop"),
            single=lambda: single,
            double=lambda: double,
        )

    def test_select_then_double_click_undoes_the_toggle(self):
        decider = self._decider(ACTION_SELECT, ACTION_DETAIL)
        decider.click("a")
        decider.double_click("a")
        self.assertEqual(self.done, [(ACTION_SELECT, "a"), (ACTION_SELECT, "a"), (ACTION_DETAIL, "a")])

    def test_other_click_actions_wait_for_a_possible_double_click(self):
        decider = self._decider(ACTION_DETAIL, ACTION_SELECT)
        decider.click("a")
        self.assertEqual(self.done, [])
        self.assertEqual(self.timer[-1], "start")
        decider.timer_fired()
        self.assertEqual(self.done, [(ACTION_DETAIL, "a")])

    def test_no_double_click_action_runs_at_once(self):
        decider = self._decider(ACTION_DETAIL, ACTION_NONE)
        decider.click("a")
        self.assertEqual(self.done, [(ACTION_DETAIL, "a")])


class StagingTests(unittest.TestCase):
    def test_notices(self):
        self.assertEqual(staging_notice({"error": "offline"}).kind, NOTICE_ERROR)
        self.assertEqual(staging_notice({"direct": True}).kind, NOTICE_SUCCESS)
        self.assertEqual(staging_notice({"staged": 0}).kind, NOTICE_WARNING)
        notice = staging_notice({"staged": 3, "ready": 2, "skip": 1})
        self.assertTrue(notice.review)
        self.assertIn("3", notice.title)

    def test_stage_reports_failures(self):
        class Manager:
            def stage_preview(self, preview, *, direct_single):
                raise RuntimeError("bad rule")

        self.assertEqual(stage(Manager(), lambda: {}, direct_single=False), {"error": "bad rule", "staged": 0})

    def test_payload_marks_foreign_stats_unknown(self):
        video = SearchVideo(video_id="oreno3d:1", title="T", source_kind="oreno3d", download_video_id="abc")
        payload = video_payload(video)
        self.assertEqual((payload["id"], payload["_stats_known"]), ("abc", False))


class QueueTests(unittest.TestCase):
    def test_selection_split_and_warnings(self):
        video = SearchVideo(video_id="v1", title="V", source_kind="iwara")
        playlist = SearchPlaylist(playlist_id="p1", title="P", source_url="https://www.iwara.tv/playlist/p1")
        selection = queue_selection([{"kind": "video", "data": video}, {"kind": "playlist", "data": playlist}])
        self.assertEqual((selection.video_ids(), selection.playlist_urls, selection.warning), (["v1"], [playlist.source_url], ""))
        self.assertTrue(queue_selection([]).warning)
        payloads = queue_payloads(["v1", "x"], selection.known_videos())
        self.assertEqual([p["id"] for p in payloads], ["v1", "x"])


class ShortcutTests(unittest.TestCase):
    def test_portable_normalize(self):
        self.assertEqual(portable_normalize("shift+ctrl+k"), "Ctrl+Shift+K")
        self.assertEqual(portable_normalize("Delete"), "Del")
        self.assertEqual(portable_normalize("Ctrl++"), "Ctrl++")
        self.assertEqual(portable_normalize("Ctrl+K, Ctrl+S"), "Ctrl+K, Ctrl+S")
        self.assertEqual(portable_normalize("bogus+K"), "")


class CommentTests(unittest.TestCase):
    def test_view_and_list_text(self):
        view = comment_view({"id": "c1", "body": "see https://x.y", "numReplies": 2, "user": {"username": "u", "name": "U"}})
        self.assertEqual((view.author_name, view.reply_count, view.avatar_key), ("U", 2, "c1"))
        self.assertIn("href", view.body_html)
        self.assertEqual(comments_hint("boom", 3), "boom")
        self.assertTrue(comments_hint("", 0))
        self.assertFalse(more_comments_available(10, 10, [{}]))
        self.assertTrue(more_comments_available(10, 5, [{}]))


if __name__ == "__main__":
    unittest.main()
