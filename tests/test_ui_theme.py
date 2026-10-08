"""Shared theme tokens, theme persistence and the small UI helpers added with them."""
import csv
import os
import tempfile
import threading
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QWidget

from qfluentwidgets import Theme, setTheme, themeColor

from app.config import app_config
from app.core.models import TaskStatus
from app.ui.download_page import split_submitted_urls
from app.ui.history_page import HISTORY_CSV_FIELDS, write_history_csv
from app.ui.theme import (
    DARK,
    LIGHT,
    apply_theme_mode,
    install_accent,
    normalize_theme_mode,
    summary_text,
    task_status_color,
)
from app.ui.ui_state import ResponsiveFlowLayout


class ThemeTokenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        install_accent()

    def tearDown(self):
        setTheme(Theme.LIGHT)

    def test_accent_follows_palette_in_both_themes(self):
        setTheme(Theme.LIGHT)
        self.assertEqual(themeColor().name(), LIGHT.accent)
        setTheme(Theme.DARK)
        # The stock ramp forces full brightness in dark mode (neon cyan).
        self.assertEqual(themeColor().name(), DARK.accent)
        self.assertLess(themeColor().valueF(), 0.95)

    def test_task_status_colors_switch_with_theme(self):
        setTheme(Theme.LIGHT)
        self.assertEqual(task_status_color(TaskStatus.FAILED).name(), LIGHT.danger)
        setTheme(Theme.DARK)
        self.assertEqual(task_status_color(TaskStatus.FAILED).name(), DARK.danger)
        self.assertEqual(task_status_color(None).name(), DARK.neutral)

    def test_theme_mode_normalization(self):
        self.assertEqual(normalize_theme_mode("DARK"), "dark")
        self.assertEqual(normalize_theme_mode("sepia"), "auto")
        self.assertEqual(normalize_theme_mode(None), "auto")

    def test_apply_theme_mode_switches_qfluent_theme(self):
        apply_theme_mode("dark")
        self.assertEqual(themeColor().name(), DARK.accent)
        apply_theme_mode("light")
        self.assertEqual(themeColor().name(), LIGHT.accent)

    def test_summary_text_joins_pairs(self):
        self.assertEqual(summary_text([("A", 1), ("B", "2/3")]), "A 1  ·  B 2/3")


class ThemeModeConfigTests(unittest.TestCase):
    def setUp(self):
        self._saved = app_config.theme_mode

    def tearDown(self):
        app_config.theme_mode = self._saved

    def test_theme_mode_round_trip_and_rejects_unknown_values(self):
        app_config.theme_mode = "dark"
        self.assertEqual(app_config.theme_mode, "dark")
        app_config.theme_mode = "neon"
        self.assertEqual(app_config.theme_mode, "auto")

    def test_concurrent_reads_and_writes_do_not_crash(self):
        errors: list[BaseException] = []

        def reader():
            try:
                for _ in range(300):
                    app_config.auto_restore_stalled_cancelled
            except BaseException as exc:  # pragma: no cover - failure path
                errors.append(exc)

        threads = [threading.Thread(target=reader) for _ in range(3)]
        for thread in threads:
            thread.start()
        for index in range(300):
            app_config.set_ui_value("theme_test_probe", index, sync=index % 50 == 0)
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])


class SubmittedUrlTests(unittest.TestCase):
    def test_multiple_urls_are_split_and_deduplicated(self):
        text = (
            "https://www.iwara.tv/video/a\nhttps://www.iwara.tv/video/b,"
            " https://www.iwara.tv/video/a；https://www.iwara.tv/profile/c"
        )
        self.assertEqual(
            split_submitted_urls(text),
            [
                "https://www.iwara.tv/video/a",
                "https://www.iwara.tv/video/b",
                "https://www.iwara.tv/profile/c",
            ],
        )

    def test_non_url_input_is_kept_for_resolver_errors(self):
        self.assertEqual(split_submitted_urls("  not a url  "), ["not a url"])
        self.assertEqual(split_submitted_urls("   "), [])


class HistoryCsvTests(unittest.TestCase):
    def test_csv_has_bom_header_and_fills_missing_source_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "history.csv")
            write_history_csv(
                path,
                [{"video_id": "abc", "title": "标题, with comma", "likes": 3}],
            )
            with open(path, "rb") as handle:
                self.assertTrue(handle.read().startswith(b"\xef\xbb\xbf"))
            with open(path, encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.reader(handle))
        self.assertEqual(tuple(rows[0]), HISTORY_CSV_FIELDS)
        row = dict(zip(rows[0], rows[1]))
        self.assertEqual(row["title"], "标题, with comma")
        self.assertEqual(row["likes"], "3")
        self.assertEqual(row["source_url"], "https://www.iwara.tv/video/abc")


class FlowLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_short_items_are_centred_on_their_row(self):
        host = QWidget()
        layout = ResponsiveFlowLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        tall = QPushButton("tall", host)
        tall.setFixedHeight(40)
        short = QLabel("short", host)
        short.setFixedHeight(20)
        layout.addWidget(tall)
        layout.addWidget(short)
        host.resize(400, 100)
        layout.setGeometry(host.rect())
        self.assertEqual(tall.geometry().top(), 0)
        self.assertEqual(short.geometry().top(), 10)


if __name__ == "__main__":
    unittest.main()
