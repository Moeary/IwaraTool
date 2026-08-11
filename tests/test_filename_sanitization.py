import os
import tempfile
import unittest

from app.config import app_config
from app.core.manager import DownloadManager


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

