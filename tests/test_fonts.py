from __future__ import annotations

import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget

from app.config import AppConfig, app_config
from app.ui import fonts
from app.ui.fonts import AUTO_FONT, build_font_stack
from app.ui.settings.sections import settings_categories


def _installed(*names):
    known = {name.casefold() for name in names}
    return lambda name: name.casefold() in known


WINDOWS = _installed("Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei", "Yu Gothic UI", "Meiryo", "Comic Sans MS")
MACOS = _installed("Helvetica Neue", "PingFang SC", "Hiragino Sans", "Menlo")


class FontStackTests(unittest.TestCase):
    def test_windows_chinese_interface(self):
        self.assertEqual(
            build_font_stack("windows", "zh", system_family="Segoe UI", available=WINDOWS),
            ["Segoe UI", "Microsoft YaHei UI", "Yu Gothic UI"],
        )

    def test_japanese_interface_puts_japanese_fonts_before_chinese(self):
        self.assertEqual(
            build_font_stack("windows", "ja", system_family="Segoe UI", available=WINDOWS),
            ["Segoe UI", "Yu Gothic UI", "Microsoft YaHei UI"],
        )

    def test_macos_never_asks_for_windows_fonts(self):
        stack = build_font_stack("macos", "zh", system_family=".AppleSystemUIFont", available=MACOS)
        self.assertEqual(stack, [".AppleSystemUIFont", "Helvetica Neue", "PingFang SC", "Hiragino Sans"])
        self.assertNotIn("Segoe UI", stack)
        self.assertNotIn("Microsoft YaHei", stack)

    def test_missing_first_choice_falls_to_the_next_installed_one(self):
        stack = build_font_stack("windows", "zh", available=_installed("Segoe UI", "Microsoft YaHei", "Meiryo"))
        self.assertEqual(stack, ["Segoe UI", "Microsoft YaHei", "Meiryo"])

    def test_chosen_font_leads_and_platform_fonts_stay_behind_it(self):
        stack = build_font_stack(
            "windows", "zh", user_family="Comic Sans MS", system_family="Segoe UI", available=WINDOWS
        )
        self.assertEqual(stack[0], "Comic Sans MS")
        self.assertIn("Microsoft YaHei UI", stack)  # CJK characters fall back here

    def test_uninstalled_choice_is_ignored(self):
        stack = build_font_stack("macos", "zh", user_family="Segoe UI", available=MACOS)
        self.assertNotIn("Segoe UI", stack)
        self.assertEqual(stack[0], "Helvetica Neue")

    def test_no_duplicates_case_insensitively(self):
        stack = build_font_stack(
            "windows", "zh", user_family="segoe ui", system_family="Segoe UI", available=WINDOWS
        )
        self.assertEqual([name.casefold() for name in stack].count("segoe ui"), 1)

    def test_unknown_platform_uses_linux_fonts(self):
        stack = build_font_stack("freebsd", "en", available=_installed("Noto Sans", "Noto Sans CJK SC"))
        self.assertEqual(stack, ["Noto Sans", "Noto Sans CJK SC"])

    def test_default_is_automatic(self):
        self.assertEqual(AppConfig._DEFAULTS["ui_font_family"], AUTO_FONT)

    def test_font_card_is_listed_under_general(self):
        general = next(cards for key, _icon, _title, _summary, cards in settings_categories() if key == "general")
        self.assertIn("font", general)


class ApplyFontTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from qfluentwidgets import fontFamilies

        self._saved = app_config.ui_font_family
        self._app_font = self.app.font()
        self._fluent_families = fontFamilies()

    def tearDown(self):
        from qfluentwidgets import setFontFamilies

        app_config.ui_font_family = self._saved
        self.app.setFont(self._app_font)
        setFontFamilies(self._fluent_families)

    def test_apply_feeds_qfluentwidgets_and_the_application_font(self):
        from qfluentwidgets import fontFamilies

        with mock.patch.object(fonts, "interface_font_families", return_value=["Font A", "Font B"]):
            families = fonts.apply_app_font(self.app)
        self.assertEqual(families, ["Font A", "Font B"])
        self.assertEqual(fontFamilies(), ["Font A", "Font B"])
        self.assertEqual(self.app.font().families()[:2], ["Font A", "Font B"])

    def test_saved_choice_reaches_the_stack(self):
        app_config.ui_font_family = "Chosen Font"
        with mock.patch.object(fonts, "font_available", return_value=True):
            self.assertEqual(fonts.interface_font_families()[0], "Chosen Font")
        app_config.ui_font_family = ""
        self.assertNotIn("Chosen Font", fonts.interface_font_families())

    def test_monospace_font_is_fixed_pitch_hint(self):
        font = fonts.monospace_font(10)
        self.assertEqual(font.pointSize(), 10)
        self.assertTrue(font.families())


class FontSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._saved = app_config.ui_font_family

    def tearDown(self):
        app_config.ui_font_family = self._saved

    def test_picker_filters_and_returns_the_choice(self):
        from app.ui.settings.font_cards import FontPickerDialog

        host = QWidget()
        host.resize(900, 700)
        dialog = FontPickerDialog(host, ["Arial", "Comic Sans MS", "Consolas"], "Consolas")
        try:
            self.assertEqual(dialog.selected_family, "Consolas")
            dialog._search.setText("comic")
            shown = [
                dialog._list.item(row).text()
                for row in range(dialog._list.count())
                if not dialog._list.item(row).isHidden()
            ]
            self.assertEqual(len(shown), 2)  # automatic + Comic Sans MS
            self.assertIn("Comic Sans MS", shown)
            dialog._list.setCurrentRow(0)
            self.assertEqual(dialog.selected_family, AUTO_FONT)
        finally:
            dialog.deleteLater()
            host.deleteLater()

    def test_unknown_current_font_selects_automatic(self):
        from app.ui.settings.font_cards import FontPickerDialog

        host = QWidget()
        dialog = FontPickerDialog(host, ["Arial"], "Gone Font")
        try:
            self.assertEqual(dialog.selected_family, AUTO_FONT)
        finally:
            dialog.deleteLater()
            host.deleteLater()

    def test_changing_the_font_saves_it_and_asks_for_a_rebuild(self):
        from app.ui.settings import font_cards

        app_config.ui_font_family = ""
        card = font_cards.SettingsFontCardsMixin()
        # Live windows elsewhere in the run listen to the real bus and would rebuild.
        with mock.patch.object(font_cards, "signal_bus") as bus:
            card._set_ui_font("Comic Sans MS")
            self.assertEqual(app_config.ui_font_family, "Comic Sans MS")
            card._set_ui_font("Comic Sans MS")  # unchanged: no second rebuild
            card._set_ui_font("")
        self.assertEqual(app_config.ui_font_family, "")
        self.assertEqual(bus.font_changed.emit.call_count, 2)


if __name__ == "__main__":
    unittest.main()
