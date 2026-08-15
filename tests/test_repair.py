from __future__ import annotations

import os
from unittest import TestCase

from app.core.repair import (
    extract_iwara_video_id,
    filename_search_text,
    format_repair_filename,
    guess_filename_video_id,
    scan_video_files,
)


class RepairHelperTests(TestCase):
    def test_extracts_only_explicit_video_ids(self):
        self.assertEqual(
            extract_iwara_video_id("https://www.iwara.tv/video/AbC_123"),
            "AbC_123",
        )
        self.assertEqual(extract_iwara_video_id("iwara-id=AbC12345"), "AbC12345")
        self.assertEqual(extract_iwara_video_id("a title with random words"), "")

    def test_filename_search_text_removes_date_quality_and_id_marker(self):
        path = "2024-05-06_Iwara ID AbC12345_My Cool Video_1080p.mp4"
        self.assertEqual(filename_search_text(path), "My Cool Video")

    def test_guess_filename_id_only_uses_trailing_long_token(self):
        self.assertEqual(
            guess_filename_video_id("2024-05-06_Demo Title_AbC12345.mp4"),
            "AbC12345",
        )
        self.assertEqual(guess_filename_video_id("A title without an id.mp4"), "")

    def test_repair_filename_keeps_source_extension_and_author_folder(self):
        path = "old-name.webm"
        result = format_repair_filename(
            "{username}/{YYYY-MM-DD}_{title}_{id}.mp4",
            {
                "video_id": "AbC12345",
                "title": "Demo Video",
                "author": "creator",
                "published_at": "2024-05-06T12:00:00Z",
            },
            path,
        )
        self.assertEqual(
            result,
            os.path.join("creator", "2024-05-06_Demo Video_AbC12345.webm"),
        )

    def test_repair_filename_does_not_escape_output_root(self):
        result = format_repair_filename(
            "../{author}/../../{title}",
            {"title": "Demo", "author": "creator"},
            "old-name.mp4",
        )
        self.assertEqual(result, os.path.join("creator", "Demo.mp4"))

    def test_scan_video_files_returns_empty_for_missing_folder(self):
        self.assertEqual(scan_video_files("this-folder-does-not-exist"), [])
