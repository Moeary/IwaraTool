from __future__ import annotations

import json
import unittest
import xml.etree.ElementTree as ET
from types import SimpleNamespace

from app.core.nfo import build_nfo_text, duration_minutes, parse_tags


class NfoGenerationTests(unittest.TestCase):
    def test_build_nfo_is_valid_movie_xml(self):
        task = SimpleNamespace(
            url="https://www.iwara.tv/video/abc123",
            video_id="abc123",
            title='A & "quoted" <title>',
            author="author_name",
            published_at="2026-06-10T12:34:56.000Z",
            duration=61,
            likes=3,
            views=7,
            comments=2,
            quality="Source",
            slug="sample-slug",
            rating="ecchi",
            raw_json=json.dumps({"body": "Line 1 & Line 2"}, ensure_ascii=False),
        )

        xml_text = build_nfo_text(task, ["mmd", "dance"])
        root = ET.fromstring(xml_text)

        self.assertEqual(root.tag, "movie")
        self.assertEqual(root.findtext("title"), 'A & "quoted" <title>')
        self.assertEqual(root.findtext("video_id"), "abc123")
        self.assertEqual(root.findtext("author"), "author_name")
        self.assertEqual(root.findtext("uniqueid"), "abc123")
        self.assertEqual(root.find("uniqueid").attrib["type"], "iwara")
        self.assertEqual(root.findtext("runtime"), "2")
        self.assertEqual(root.findtext("duration"), "61")
        self.assertEqual(root.findtext("rating"), "ecchi")
        self.assertEqual(root.findtext("likes"), "3")
        self.assertEqual(root.findtext("views"), "7")
        self.assertEqual(root.findtext("comments"), "2")
        self.assertEqual(root.findtext("premiered"), "2026-06-10")
        self.assertEqual([node.text for node in root.findall("tag")], ["mmd", "dance"])
        self.assertIsNone(root.find("tags_json"))
        self.assertIsNone(root.find("author_message"))

    def test_parse_tags_prefers_human_labels_and_deduplicates(self):
        tags_json = json.dumps(
            [
                {"id": "mmd", "name": "MMD"},
                {"type": "mmd"},
                {"slug": "dance"},
                "Dance",
            ]
        )
        self.assertEqual(parse_tags(tags_json), ["MMD", "dance"])

    def test_duration_minutes_rounds_up(self):
        self.assertEqual(duration_minutes(0), 0)
        self.assertEqual(duration_minutes(1), 1)
        self.assertEqual(duration_minutes(60), 1)
        self.assertEqual(duration_minutes(61), 2)


if __name__ == "__main__":
    unittest.main()
