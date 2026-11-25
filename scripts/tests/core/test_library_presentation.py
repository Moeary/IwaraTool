"""Library (history) rules shared by both interfaces."""

from __future__ import annotations

import csv
import os
import tempfile
import unittest

from app.presentation.history import library as lib


def state_of(record):
    key = record.get("_state", "ok")
    return ({"ok": "OK", "moved": "Moved", "remote": "NAS"}[key], key, "")


RECORDS = [
    {"video_id": "a", "title": "Alpha", "author": "ann", "likes": 5, "downloaded_at": "2026-11-20 10:00:00"},
    {"video_id": "b", "title": "Beta", "author": "bob", "likes": 50, "downloaded_at": "2020-01-01 10:00:00",
     "watched_at": "2026-01-01", "_state": "moved"},
    {"video_id": "c", "title": "Gamma", "author": "ann", "likes": 7, "downloaded_at": "2026-11-21 10:00:00",
     "_state": "remote"},
]


class LibraryRulesTests(unittest.TestCase):
    def test_counts_and_filters(self):
        visible, counts = lib.filter_records(RECORDS, state_of=state_of, cutoff="2026-11-01 00:00:00")
        self.assertEqual(len(visible), 3)
        self.assertEqual((counts.ok, counts.moved, counts.remote, counts.unwatched), (1, 1, 1, 2))
        visible, _ = lib.filter_records(RECORDS, state_of=state_of, scope=lib.SCOPE_RECENT, cutoff="2026-11-01 00:00:00")
        self.assertEqual([r["video_id"] for r in visible], ["a", "c"])
        visible, _ = lib.filter_records(RECORDS, state_of=state_of, state_filter=lib.STATE_MOVED)
        self.assertEqual([r["video_id"] for r in visible], ["b"])
        visible, _ = lib.filter_records(RECORDS, state_of=state_of, scope=lib.SCOPE_AUTHOR, author="ann")
        self.assertEqual([r["video_id"] for r in visible], ["a", "c"])
        visible, _ = lib.filter_records(RECORDS, state_of=state_of, query="gam", field=lib.SEARCH_FIELDS.index("title"))
        self.assertEqual([r["video_id"] for r in visible], ["c"])

    def test_sorting(self):
        ordered = lib.sort_records(list(RECORDS), "likes", True, state_of=state_of)
        self.assertEqual([r["video_id"] for r in ordered], ["b", "c", "a"])
        ordered = lib.sort_records(list(RECORDS), "state", False, state_of=state_of)
        self.assertEqual(ordered[0]["video_id"], "a")
        self.assertIn("likes", lib.DESCENDING_FIRST)

    def test_authors_by_name_and_count(self):
        counts = {"file10": 1, "file2": 3, "Bob": 2, "": 4}
        self.assertEqual([n for n, _ in lib.sort_authors(counts, lib.AUTHOR_SORT_NAME, language="en")], ["Bob", "file2", "file10"])
        self.assertEqual(lib.sort_authors(counts, lib.AUTHOR_SORT_COUNT, language="en")[0], ("file2", 3))

    def test_csv_export(self):
        path = os.path.join(tempfile.mkdtemp(), "x.csv")
        lib.write_history_csv(path, RECORDS[:1])
        with open(path, encoding="utf-8-sig") as handle:
            rows = list(csv.reader(handle))
        self.assertEqual(tuple(rows[0]), lib.HISTORY_CSV_FIELDS)
        self.assertTrue(rows[1][-1].endswith("/video/a"))

    def test_record_card_prefers_the_local_thumbnail(self):
        card = lib.record_card({"video_id": "abc", "title": "T", "thumbnail_path": "/t.jpg"})
        self.assertEqual((card.key, card.history_id, card.cover_local), ("record:abc", "abc", "/t.jpg"))
        self.assertIn("2026-11-20", lib.record_line(RECORDS[0]))

    def test_remove_prompt_lists_at_most_five(self):
        prompt = lib.remove_prompt([f"t{i}" for i in range(7)])
        self.assertIn("t4", prompt)
        self.assertNotIn("t5", prompt)


if __name__ == "__main__":
    unittest.main()
