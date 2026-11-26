"""core.search.resolution without Qt: queue IDs and staged Oreno3D link resolution."""

from __future__ import annotations

import threading
import unittest

from app.core.search.models import SearchVideo
from app.core.search.resolution import OrenoLinkResolver, resolve_oreno_video_id, resolve_queue_ids


def _oreno(source_id: str, title: str = "") -> SearchVideo:
    return SearchVideo(
        video_id=f"oreno3d:{source_id}",
        title=title or source_id,
        source_kind="oreno3d",
        source_url=f"https://oreno3d.com/movies/{source_id}",
        downloadable=False,
    )


class FakeManager:
    """Implements the full resolver signatures; ``dead`` cards have no Iwara video."""

    def __init__(self, dead=()):
        self.dead = set(dead)
        self.author_calls = []
        self.lock = threading.Lock()

    def resolve_oreno3d_video_id(self, source_id, oreno3d_url, *, parallel=False):
        return "" if source_id in self.dead else f"iwara-{source_id}"

    def resolve_oreno3d_video_details(self, source_id, oreno3d_url, *, parallel=False):
        return {
            "video_id": self.resolve_oreno3d_video_id(source_id, oreno3d_url, parallel=parallel),
            "oreno_author_url": "https://oreno3d.com/authors/1",
            "oreno_author_name": "Maker",
        }

    def get_iwara_video_info(self, video_id):
        return {"id": video_id, "user": {"username": "maker"}}, ""

    def resolve_oreno3d_author(self, source_id="", oreno3d_url="", *, author_url="", author_name="", max_videos=8, parallel=False, **_):
        with self.lock:
            self.author_calls.append(author_url)
        return {"iwara_author": {"username": "maker"}}


class QueueTests(unittest.TestCase):
    def test_iwara_cards_pass_through_and_oreno_cards_resolve(self):
        videos = [
            SearchVideo(video_id="abc", title="Iwara", source_kind="iwara"),
            _oreno("1"),
            _oreno("2"),
            SearchVideo(video_id="x", title="Other", source_kind="other"),
        ]
        result = resolve_queue_ids(FakeManager(dead={"2"}), videos, should_stop=lambda: False)
        self.assertEqual(sorted(result["ids"]), ["abc", "iwara-1"])
        self.assertEqual(result["skipped"], 2)
        self.assertEqual(result["errors"], [])

    def test_known_links_need_no_request(self):
        video = _oreno("1")
        video.iwara_url = "https://www.iwara.tv/video/known/title"
        self.assertEqual(resolve_oreno_video_id(object(), video), "known")


class LinkResolverTests(unittest.TestCase):
    def test_stages_progress_and_shared_author_lookup(self):
        manager = FakeManager(dead={"2", "3"})
        events, progress = [], []
        result = OrenoLinkResolver(
            manager, 5, [_oreno("1"), _oreno("2"), _oreno("3")],
            on_item=events.append,
            on_progress=lambda done, total: progress.append((done, total)),
            should_stop=lambda: False,
        ).run()

        links = result["links"]
        self.assertEqual(result["generation"], 5)
        self.assertEqual(links["oreno3d:1"]["metadata"]["id"], "iwara-1")
        self.assertEqual(links["oreno3d:2"]["iwara_author"], {"username": "maker"})
        self.assertEqual(manager.author_calls, ["https://oreno3d.com/authors/1"])  # one lookup for both dead cards
        self.assertEqual(len(result["errors"]), 2)
        self.assertEqual(progress[0], (0, 3))
        self.assertEqual(progress[-1], (3, 3))
        stages = {(e["video_id"], e["stage"]) for e in events}
        self.assertIn(("oreno3d:1", "metadata"), stages)
        self.assertIn(("oreno3d:2", "source"), stages)
        self.assertIn(("oreno3d:3", "author"), stages)

    def test_nothing_is_resolved_after_stopping(self):
        result = OrenoLinkResolver(FakeManager(), 1, [_oreno("1")], should_stop=lambda: True).run()
        self.assertEqual(result["links"], {})

    def test_empty_page(self):
        progress = []
        result = OrenoLinkResolver(FakeManager(), 2, [], on_progress=lambda *a: progress.append(a)).run()
        self.assertEqual((result["links"], progress), ({}, [(0, 0)]))


if __name__ == "__main__":
    unittest.main()
