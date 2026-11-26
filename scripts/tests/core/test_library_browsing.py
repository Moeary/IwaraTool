"""The library's NFO tags, record editing and relocating (core and presentation, no UI)."""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from app.core.nfo import nfo_for_video, nfo_path_for, parse_nfo_text
from app.presentation.history import library as lib

NFO = """<?xml version="1.0" encoding="utf-8"?>
<movie><title>T</title><plot>Story</plot><genre>MMD</genre><genre>dance</genre><tag>mmd</tag><tag>Favorite</tag>
<actor><name>Miku</name></actor><director>ann</director><studio>ann</studio><premiered>2025-03-01</premiered></movie>"""


class NfoReadingTests(unittest.TestCase):
    def test_tags_and_people_are_unique_and_keep_the_first_spelling(self):
        info = parse_nfo_text(NFO)
        self.assertEqual(info.tags, ("MMD", "dance", "Favorite"))
        self.assertEqual(info.people, ("Miku", "ann"))
        self.assertEqual((info.title, info.plot, info.year), ("T", "Story", "2025"))
        self.assertIsNone(parse_nfo_text("not xml"))

    def test_the_nfo_beside_a_video_is_found_and_reread_after_a_change(self):
        with tempfile.TemporaryDirectory() as folder:
            video = os.path.join(folder, "a_b.mp4")
            self.assertEqual(nfo_path_for(video), "")
            movie = os.path.join(folder, "movie.nfo")
            with open(movie, "w", encoding="utf-8") as handle:
                handle.write("<movie><genre>x</genre></movie>")
            self.assertEqual(nfo_path_for(video), movie)
            own = os.path.join(folder, "a_b.nfo")
            with open(own, "w", encoding="utf-8") as handle:
                handle.write(NFO)
            self.assertEqual(nfo_for_video(video).tags[0], "MMD")
            with open(own, "w", encoding="utf-8") as handle:
                handle.write("<movie><genre>changed</genre><genre>again</genre></movie>")
            self.assertEqual(nfo_for_video(video).tags, ("changed", "again"))


class TagFilterTests(unittest.TestCase):
    def setUp(self):
        self.records = [{"video_id": f"v{i}", "title": f"T{i}"} for i in range(4)]
        self.tags = {"v0": ("MMD", "dance"), "v1": ("mmd",), "v2": ("Dance",)}
        self.tags_of = lambda record: self.tags.get(record["video_id"], ())
        self.state_of = lambda _record: ("ok", "ok", "")

    def ids(self, **kwargs):
        visible, _counts = lib.filter_records(self.records, state_of=self.state_of, tags_of=self.tags_of, **kwargs)
        return [r["video_id"] for r in visible]

    def test_every_chosen_tag_must_match(self):
        self.assertEqual(self.ids(tags=["mmd"]), ["v0", "v1"])
        self.assertEqual(self.ids(tags=["mmd", "DANCE"]), ["v0"])
        self.assertEqual(self.ids(), ["v0", "v1", "v2", "v3"])

    def test_the_all_field_query_matches_a_tag(self):
        self.assertEqual(self.ids(query="danc"), ["v0", "v2"])

    def test_counts_and_merging(self):
        self.assertEqual(lib.tag_counts(self.records, self.tags_of), [("dance", 2), ("MMD", 2)])
        self.assertEqual(lib.merge_tags(["MMD", "x"], ["mmd", "y"]), ("MMD", "x", "y"))


class RecordEditingTests(unittest.TestCase):
    def setUp(self):
        from app.core.history import DownloadHistory

        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        emit = mock.patch("app.core.history.signal_bus")  # a Qt signal's emit cannot be patched
        emit.start()
        self.addCleanup(emit.stop)
        self.history = DownloadHistory(os.path.join(self.folder.name, "history.db"))
        self.history.upsert_downloaded({"video_id": "v1", "title": "Old", "author": "a",
                                        "tags_json": '[{"id": "mmd"}, "dance"]', "file_path": "/gone/v1.mp4"})
        self.history.upsert_downloaded({"video_id": "v2", "title": "Two"})

    def test_only_editable_columns_change(self):
        self.assertTrue(self.history.update_details("v1", {"title": " New ", "file_path": "/etc/passwd"}))
        record = self.history.get_record("v1")
        self.assertEqual((record["title"], record["file_path"]), ("New", "/gone/v1.mp4"))
        self.assertFalse(self.history.update_details("v1", {"file_path": "x"}))
        self.assertFalse(self.history.update_details("missing", {"title": "x"}))

    def test_saved_tags_without_the_heavy_columns(self):
        self.assertEqual(self.history.tags_by_id(), {"v1": ["mmd", "dance"]})

    def test_relocating_points_the_record_at_the_file_and_its_thumbnail(self):
        from app.core.download.history_actions import HistoryActionsMixin

        actions = HistoryActionsMixin()
        actions.history = self.history
        video = os.path.join(self.folder.name, "moved.mp4")
        for path in (video, os.path.join(self.folder.name, "moved.jpg")):
            with open(path, "wb") as handle:
                handle.write(b"x")
        self.assertFalse(actions.relocate_history_file("v1", video + ".missing")[0])
        ok, path = actions.relocate_history_file("v1", video)
        self.assertTrue(ok)
        record = self.history.get_record("v1")
        self.assertEqual(record["file_path"], os.path.abspath(video))
        self.assertTrue(record["thumbnail_path"].endswith("moved.jpg"))


if __name__ == "__main__":
    unittest.main()
