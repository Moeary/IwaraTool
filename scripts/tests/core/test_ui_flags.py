"""Saved on/off UI values come back from the INI file as strings."""

import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.config import app_config


class UiFlagTests(unittest.TestCase):
    def test_strings_from_the_ini_file_read_as_booleans(self):
        for stored, expected in (("false", False), ("true", True), ("0", False), ("1", True), (False, False), (True, True)):
            with mock.patch.object(type(app_config), "get_ui_value", return_value=stored):
                self.assertIs(app_config.get_ui_flag("x", True), expected, stored)


if __name__ == "__main__":
    unittest.main()
