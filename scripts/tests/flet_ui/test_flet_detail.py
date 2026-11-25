"""The detail store against a fake manager (no window, no network)."""

from __future__ import annotations

import asyncio
import importlib.util
import unittest
from types import SimpleNamespace
from unittest import mock

from app.presentation.media.detail_text import link_segments

HAS_FLET = importlib.util.find_spec("flet") is not None


def info(video_id: str = "v1", **extra) -> dict:
    data = {
        "id": video_id, "title": "Title", "body": "see https://example.com/x", "numLikes": 10, "numViews": 100,
        "liked": False, "user": {"id": "u1", "username": "ann", "name": "Ann"},
        "file": {"id": "f1", "height": 1080}, "tags": [{"id": "dance"}, {"id": "uncategorized"}],
        "createdAt": "2026-01-02T00:00:00Z", "rating": "general",
    }
    data.update(extra)
    return data


class _Client:
    def __init__(self, owner):
        self.owner = owner

    def get_video_info(self, video_id):
        return self.owner.info, self.owner.info_error

    def set_liked(self, kind, item_id, wanted):
        self.owner.likes.append((kind, item_id, wanted))
        return self.owner.like_result


class _Manager:
    def __init__(self):
        self.info = info()
        self.info_error = ""
        self.related = [info("r1", title="Related")]
        self.comment_pages = {0: [{"id": "c1", "body": "hi", "numReplies": 2, "user": {"username": "bob"}}]}
        self.replies = {"c1": [{"id": "c2", "body": "reply", "user": {"username": "cy"}}]}
        self.likes = []
        self.like_result = (True, "")
        self.tag_dictionary = None
        self.history = SimpleNamespace()

    def create_worker_api_client(self):
        return _Client(self)

    def close_worker_api_client(self, client):
        pass

    def get_related_items(self, kind, item_id, api_client=None):
        return self.related, ""

    def get_item_comments(self, kind, item_id, page=0, parent="", api_client=None):
        if parent:
            return self.replies.get(parent, []), len(self.replies.get(parent, [])), ""
        return self.comment_pages.get(page, []), 2, ""


class _Page:
    def run_task(self, fn, *args):
        return asyncio.ensure_future(fn(*args))


class LinkSegmentTests(unittest.TestCase):
    def test_splits_urls_from_text(self):
        self.assertEqual(
            link_segments("a https://x.y/z b"),
            [("a ", ""), ("https://x.y/z", "https://x.y/z"), (" b", "")],
        )
        self.assertEqual(link_segments(""), [])


@unittest.skipUnless(HAS_FLET, "Flet is not installed in this environment")
class DetailStoreTests(unittest.TestCase):
    def make(self, *, logged_in: bool = True):
        from app.ui_flet.stores.detail import DetailStore

        self.manager = _Manager()
        self.shell = SimpleNamespace(page=_Page(), state=SimpleNamespace(logged_in=logged_in))
        return DetailStore(self.shell, self.manager)

    def test_loads_record_related_and_comments(self):
        store = self.make()
        model = store.model("video", "v1")
        asyncio.run(model.start())
        state = model.state
        self.assertFalse(state.loading)
        self.assertEqual(state.video.title, "Title")
        self.assertEqual(state.author.username, "ann")
        self.assertEqual([card.key for card in state.related], ["video:r1"])
        self.assertEqual([c.id for c in state.comments], ["c1"])
        self.assertEqual(model.tags(), [("dance", "#dance")])
        self.assertEqual(model.quality(), "1080p")
        self.assertIn("https://example.com/x", model.description())
        self.assertEqual(state.comment_total, 2)

    def test_failed_record_shows_error_and_retries_later(self):
        store = self.make()
        self.manager.info, self.manager.info_error = None, "gone"
        model = store.model("video", "v1")
        asyncio.run(model.start())
        self.assertIn("gone", model.state.error)
        self.assertFalse(model.started)

    def test_back_to_a_post_reuses_its_model(self):
        store = self.make()
        first = store.model("video", "v1")
        asyncio.run(first.start())
        self.assertIs(store.model("video", "v1"), first)
        with mock.patch("app.ui_flet.stores.detail.load_detail") as loader:
            asyncio.run(first.start())
        loader.assert_not_called()

    def test_replies_load_once(self):
        store = self.make()
        model = store.model("video", "v1")
        asyncio.run(model.start())
        asyncio.run(model.load_replies("c1"))
        self.assertEqual([c.id for c in model.state.replies["c1"]], ["c2"])

    def test_like_needs_sign_in(self):
        store = self.make(logged_in=False)
        model = store.model("video", "v1")
        asyncio.run(model.start())
        with mock.patch("app.ui_flet.services.notify.notice") as shown:
            asyncio.run(model.toggle_like())
        shown.assert_called_once()
        self.assertEqual(self.manager.likes, [])

    def test_like_updates_count(self):
        store = self.make()
        model = store.model("video", "v1")
        asyncio.run(model.start())
        asyncio.run(model.toggle_like())
        self.assertEqual(self.manager.likes, [("video", "v1", True)])
        self.assertTrue(model.state.liked)
        self.assertEqual(model.state.video.likes, 11)

    def test_keeps_only_recent_posts(self):
        from app.ui_flet.stores.detail import KEEP_POSTS

        store = self.make()
        first = store.model("video", "v0")
        for index in range(1, KEEP_POSTS + 1):
            store.model("video", f"v{index}")
        self.assertIsNot(store.model("video", "v0"), first)


if __name__ == "__main__":
    unittest.main()
