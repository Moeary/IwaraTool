from __future__ import annotations

import os
from unittest import TestCase

from app.core.repair.matching import (
    extract_iwara_video_id,
    filename_search_text,
    filename_video_id_guesses,
    format_repair_filename,
    guess_filename_video_id,
    parse_manual_video_id,
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

    def test_iwara_prefix_is_not_an_id_label(self):
        # Issue #32: the first title word after "Iwara - " became the "ID".
        for stem in ("Iwara - SEXDance", "Iwara-Stupid! [HMV]", "Iwara - TimeStop", "Iwara_SexDanceQOS"):
            with self.subTest(stem=stem):
                self.assertEqual(extract_iwara_video_id(stem), "")
        self.assertEqual(extract_iwara_video_id("Iwara ID AbC12345 title"), "AbC12345")
        self.assertEqual(extract_iwara_video_id("title video_id: AbC12345"), "AbC12345")

    def test_bracketed_ids_of_browser_downloads_are_guessed_first(self):
        cases = {
            "Iwara-Stupid! [HMV] [7rntP6twy92J0R] [Source].mp4": "7rntP6twy92J0R",
            "Iwara - Robin\U0001F495SEX_DANCE \U0001F495A \U0001F495Rainbow-final [jxYoGoArkoYkR2] [Source].mp4": "jxYoGoArkoYkR2",
            "MMD 2024 Miku (Lq7dXfR0vxfqRV).mp4": "Lq7dXfR0vxfqRV",
            "Dance [1080p] [Source].mp4": "",
            "Iwara - TimeStop.mp4": "",
            "Iwara - SexDanceQOS [Source].mp4": "",
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(guess_filename_video_id(name), expected)
        self.assertEqual(
            filename_video_id_guesses("Clip 2024ver01 [AbCdEf123456]_Zz9yX8wV7u.mp4")[0],
            "AbCdEf123456",
        )

    def test_search_text_drops_iwara_prefix_ids_and_quality_brackets(self):
        self.assertEqual(
            filename_search_text("Iwara-Stupid! [HMV] [7rntP6twy92J0R] [Source].mp4"),
            "Stupid! [HMV]",
        )
        self.assertEqual(filename_search_text("Iwara - TimeStop [Source].mp4"), "TimeStop")

    def test_manual_ids_accept_both_sites_and_bare_ids_only(self):
        self.assertEqual(parse_manual_video_id(" https://www.iwara.tv/video/7rntP6twy92J0R/stupid "), "7rntP6twy92J0R")
        self.assertEqual(parse_manual_video_id("https://www.iwara.ai/video/7rntP6twy92J0R"), "7rntP6twy92J0R")
        self.assertEqual(parse_manual_video_id("www.iwara.tv/video/7rntP6twy92J0R"), "7rntP6twy92J0R")
        self.assertEqual(parse_manual_video_id("jxYoGoArkoYkR2"), "jxYoGoArkoYkR2")
        self.assertEqual(parse_manual_video_id("https://notiwara.tv/video/7rntP6twy92J0R"), "")
        self.assertEqual(parse_manual_video_id("https://iwara.tv:pw@example.com/video/7rntP6twy92J0R"), "")
        self.assertEqual(parse_manual_video_id("ftp://www.iwara.tv/video/7rntP6twy92J0R"), "")
        self.assertEqual(parse_manual_video_id("https://www.iwara.tv/profile/someone"), "")
        self.assertEqual(parse_manual_video_id("not an id"), "")
        self.assertEqual(parse_manual_video_id(""), "")

    def test_title_dots_and_title_words_do_not_hide_the_bracketed_id(self):
        # A scanned stem has no extension left; ".5 [...]" is title text.
        self.assertEqual(guess_filename_video_id("Iwara - Part 1.5 [7rntP6twy92J0R] [Source]"), "7rntP6twy92J0R")
        self.assertEqual(filename_search_text("Iwara - Part 1.5 [7rntP6twy92J0R] [Source]"), "Part 1.5")
        self.assertEqual(extract_iwara_video_id("Iwara - My ID Challenge [7rntP6twy92J0R]"), "")
        self.assertEqual(extract_iwara_video_id("clip id=AbC12345"), "AbC12345")
        self.assertEqual(guess_filename_video_id("Iwara - My ID Challenge [7rntP6twy92J0R].mp4"), "7rntP6twy92J0R")

    def test_bracketed_title_words_survive_when_ids_are_kept(self):
        self.assertEqual(filename_search_text("Dance [2024Edition].mp4"), "Dance")
        self.assertEqual(filename_search_text("Dance [2024Edition].mp4", strip_ids=False), "Dance [2024Edition]")

    def test_underscored_trailing_word_is_still_a_last_resort_id(self):
        self.assertEqual(
            filename_video_id_guesses("2024-05-06_Demo Title_abcdefghij.mp4"), ["abcdefghij"],
        )
        self.assertEqual(filename_video_id_guesses("Iwara - TimeStop.mp4"), [])

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
