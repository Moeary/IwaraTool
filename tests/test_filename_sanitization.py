import os
import tempfile
import unittest

from app.config import DEFAULT_FILENAME_TEMPLATE, app_config
from app.core.download_paths import validate_filename_template
from app.core.manager import DownloadManager
from app.core.rules import normalize_rule_payload
from app.core.task_metadata import author_fields_from_user


class FilenameSanitizationTests(unittest.TestCase):
    def setUp(self):
        self.download_dir = app_config.download_dir
        self.filename_template = app_config.filename_template

    def tearDown(self):
        app_config.download_dir = self.download_dir
        app_config.filename_template = self.filename_template

    def test_sanitize_path_segment_replaces_windows_invalid_characters(self):
        cleaned = DownloadManager._sanitize_path_segment('bad<>:"/\\|?*\x00\t\r\nname')

        self.assertFalse(any(char in cleaned for char in '<>:"/\\|?*\x00\t\r\n'))
        self.assertEqual(DownloadManager._sanitize_path_segment("CON.txt"), "_CON.txt")
        self.assertEqual(DownloadManager._sanitize_path_segment("  title. "), "title")

    def test_long_title_output_path_is_shortened_and_retains_video_id(self):
        app_config.download_dir = os.path.join(tempfile.gettempdir(), "IwaraTool", "downloads")
        app_config.filename_template = "{username}/{YYYY-MM-DD}_{title}_{id}.mp4"
        manager = object.__new__(DownloadManager)
        video_id = "uOSZmxcpkKZ273"
        relative_path = manager._build_output_relative_path(
            title="警告⚠" * 150,
            video_id=video_id,
            author="maplehut",
            published_at="2024-12-25T00:00:00.000Z",
            quality="Source",
            likes=0,
            views=0,
            comments=0,
            duration=0,
            slug="",
            rating="",
        )
        output_path = os.path.abspath(os.path.join(app_config.download_dir, relative_path))

        self.assertLessEqual(DownloadManager._windows_path_length(output_path), 240)
        self.assertTrue(os.path.basename(relative_path).endswith(f"{video_id}.mp4"))

    def test_standard_author_id_and_date_tokens_are_expanded(self):
        manager = object.__new__(DownloadManager)
        relative_path = manager._build_output_relative_path(
            title="Dance",
            video_id="iwara01",
            author="alice",
            published_at="2024-12-25T00:00:00.000Z",
            quality="Source",
            likes=0,
            views=0,
            comments=0,
            duration=0,
            slug="",
            rating="",
            filename_template="HMV/{id}_{title}_{author}_{YYYY-MM-DD}_{YYYY}{MM}{DD}.mp4",
        )

        self.assertEqual(
            relative_path,
            os.path.join("HMV", "iwara01_Dance_alice_2024-12-25_20241225.mp4"),
        )

    def test_author_and_username_tokens_keep_stable_and_display_values_separate(self):
        manager = object.__new__(DownloadManager)
        relative_path = manager._build_output_relative_path(
            title="Dance",
            video_id="iwara01",
            author="user154126",
            username="这位赤身肉",
            published_at="2024-12-25T00:00:00.000Z",
            quality="Source",
            likes=0,
            views=0,
            comments=0,
            duration=0,
            slug="",
            rating="",
            filename_template="{author}/{username}_{id}.mp4",
        )

        self.assertEqual(
            relative_path,
            os.path.join("user154126", "这位赤身肉_iwara01.mp4"),
        )

    def test_user_profile_fields_use_stable_handle_and_display_name(self):
        self.assertEqual(
            author_fields_from_user(
                {"id": "profile-id", "username": "user154126", "name": "这位赤身肉"}
            ),
            ("user154126", "这位赤身肉"),
        )

    def test_filename_template_validation_rejects_unknown_or_escaping_values(self):
        self.assertEqual(validate_filename_template("HMV/{id}_{title}_{author}.mp4"), (True, ""))
        valid, reason = validate_filename_template("{unknown}_{title}.mp4")
        self.assertFalse(valid)
        self.assertIn("Unknown placeholder", reason)
        valid, reason = validate_filename_template("../{title}.mp4")
        self.assertFalse(valid)
        self.assertIn("path segments", reason)

    def test_rule_defaults_include_history_recording_and_can_disable_it(self):
        normalized = normalize_rule_payload({"record_to_history": False})

        self.assertFalse(normalized["record_to_history"])
        self.assertTrue(normalize_rule_payload({})["record_to_history"])
        self.assertEqual(normalize_rule_payload({})["filename_template"], DEFAULT_FILENAME_TEMPLATE)
