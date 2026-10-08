from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from app.config import app_config
from app.core import shortcut_defs as defs
from app.ui import shortcuts
from app.ui.settings_sections import settings_categories
from app.ui.settings_shortcuts_cards import ShortcutCaptureButton, shortcut_card_keys


class _OverridesMixin:
    """Run against a clean override table and restore the user's afterwards."""

    def setUp(self):
        self._saved = app_config.shortcut_overrides
        app_config.shortcut_overrides = "{}"

    def tearDown(self):
        app_config.shortcut_overrides = self._saved


def _press(key, modifiers=Qt.KeyboardModifier.NoModifier):
    return QKeyEvent(QEvent.Type.KeyPress, key, modifiers)


class CatalogueTests(unittest.TestCase):
    def test_ids_are_unique_and_scopes_known(self):
        actions = defs.all_actions()
        self.assertEqual(len({a.id for a in actions}), len(actions))
        self.assertTrue({a.scope for a in actions} <= set(defs.scope_titles()))

    def test_defaults_do_not_conflict_where_scopes_overlap(self):
        actions = defs.all_actions()
        for first in actions:
            for second in actions:
                if first.id >= second.id or not defs.scopes_overlap(first.scope, second.scope):
                    continue
                self.assertNotEqual(
                    defs.normalize(first.default),
                    defs.normalize(second.default),
                    f"{first.id} and {second.id} share {first.default}",
                )

    def test_every_default_is_a_valid_sequence(self):
        for action in defs.all_actions():
            self.assertTrue(defs.normalize(action.default), action.id)

    def test_scope_overlap_rules(self):
        self.assertTrue(defs.scopes_overlap("search", "search"))
        self.assertTrue(defs.scopes_overlap("global", "history"))
        self.assertFalse(defs.scopes_overlap("search", "history"))
        self.assertFalse(defs.scopes_overlap("global", "player"))

    def test_settings_page_lists_every_scope(self):
        keys = {card for category in settings_categories() for card in category[4]}
        self.assertTrue(set(shortcut_card_keys()) <= keys)


class OverrideTests(_OverridesMixin, unittest.TestCase):
    def test_default_then_override_then_reset(self):
        self.assertEqual(defs.key_for("search_queue"), "Ctrl+D")
        self.assertIsNone(defs.set_key("search_queue", "Ctrl+Shift+Q"))
        self.assertEqual(defs.key_for("search_queue"), "Ctrl+Shift+Q")
        self.assertIsNone(defs.reset_key("search_queue"))
        self.assertEqual(defs.key_for("search_queue"), "Ctrl+D")

    def test_unassign(self):
        self.assertIsNone(defs.set_key("search_queue", ""))
        self.assertEqual(defs.key_for("search_queue"), "")

    def test_same_scope_conflict_is_refused(self):
        conflict = defs.set_key("search_queue", "Ctrl+P")  # taken by search_preview
        self.assertEqual(conflict.id, "search_preview")
        self.assertEqual(defs.key_for("search_queue"), "Ctrl+D")

    def test_global_conflicts_with_pages_but_pages_may_share(self):
        self.assertEqual(defs.set_key("search_queue", "Ctrl+1").id, "nav_download")
        self.assertIsNone(defs.set_key("history_reload", "Ctrl+D"))  # search and subscriptions use it too

    def test_reset_is_refused_when_the_default_is_now_taken(self):
        self.assertIsNone(defs.set_key("search_queue", ""))
        self.assertIsNone(defs.set_key("search_preview", "Ctrl+D"))
        self.assertEqual(defs.reset_key("search_queue").id, "search_preview")

    def test_normalization_and_garbage_storage(self):
        self.assertEqual(defs.normalize("ctrl+d"), "Ctrl+D")
        self.assertEqual(defs.normalize(""), "")
        app_config.shortcut_overrides = "{broken"
        self.assertEqual(defs.key_for("search_queue"), "Ctrl+D")

    def test_unknown_action(self):
        with self.assertRaises(KeyError):
            defs.set_key("nope", "Ctrl+K")
        self.assertEqual(defs.key_for("nope"), "")

    def test_reset_all(self):
        defs.set_key("search_queue", "Ctrl+Shift+Q")
        defs.reset_all()
        self.assertEqual(defs.key_for("search_queue"), "Ctrl+D")


class RegistryTests(_OverridesMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def test_rebinding_updates_live_shortcuts(self):
        registry = shortcuts.ShortcutRegistry()
        widget = QWidget()
        calls = []
        registry.bind(widget, {"search_queue": lambda: calls.append("q")})
        shortcut = registry._bound["search_queue"][0]
        self.assertEqual(shortcut.key().toString(), "Ctrl+D")
        defs.set_key("search_queue", "Ctrl+Shift+Q")
        registry.refresh()
        self.assertEqual(shortcut.key().toString(), "Ctrl+Shift+Q")
        shortcut.activated.emit()
        self.assertEqual(calls, ["q"])
        defs.set_key("search_queue", "")
        registry.refresh()
        self.assertTrue(shortcut.key().isEmpty())

    def test_action_for_event_follows_overrides(self):
        self.assertEqual(shortcuts.action_for_event("player", _press(Qt.Key.Key_Space)), "player_play_pause")
        self.assertEqual(shortcuts.action_for_event("player", _press(Qt.Key.Key_F)), "player_fullscreen")
        self.assertEqual(shortcuts.action_for_event("search", _press(Qt.Key.Key_Space)), "")
        defs.set_key("player_play_pause", "K")
        self.assertEqual(shortcuts.action_for_event("player", _press(Qt.Key.Key_K)), "player_play_pause")
        self.assertEqual(shortcuts.action_for_event("player", _press(Qt.Key.Key_Space)), "")
        self.assertEqual(shortcuts.sequence_from_event(_press(Qt.Key.Key_Shift)), "")


class CaptureButtonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def _button(self):
        button = ShortcutCaptureButton()
        button.show()
        self.addCleanup(button.deleteLater)
        chosen = []
        button.sequenceChosen.connect(chosen.append)
        return button, chosen

    def test_records_a_combination(self):
        button, chosen = self._button()
        button.set_sequence("Ctrl+D")
        button.click()
        QTest.keyClick(button, Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(chosen, ["Ctrl+Shift+K"])

    def test_lone_modifier_keeps_waiting_then_escape_cancels(self):
        button, chosen = self._button()
        button.click()
        QTest.keyClick(button, Qt.Key.Key_Control)
        self.assertTrue(button._capturing)
        QTest.keyClick(button, Qt.Key.Key_Escape)
        self.assertFalse(button._capturing)
        self.assertEqual(chosen, [])

    def test_backspace_clears(self):
        button, chosen = self._button()
        button.click()
        QTest.keyClick(button, Qt.Key.Key_Backspace)
        self.assertEqual(chosen, [""])

    def test_shortcut_override_is_swallowed_only_while_capturing(self):
        button, _ = self._button()
        event = QKeyEvent(QEvent.Type.ShortcutOverride, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
        event.ignore()
        button.event(event)
        self.assertFalse(event.isAccepted())
        button.click()
        event.ignore()
        button.event(event)
        self.assertTrue(event.isAccepted())

    def test_button_text(self):
        button, _ = self._button()
        button.set_sequence("")
        unassigned = button.text()
        self.assertTrue(unassigned)
        button.set_sequence("Ctrl+D")
        self.assertIn("D", button.text())
        self.assertNotEqual(button.text(), unassigned)


class MainWindowBindingTests(_OverridesMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def test_navigation_and_page_actions_are_bound(self):
        from app.ui.history_page import HistoryInterface
        from app.ui.main_window import MainWindow

        # Handlers are bound at construction, so patch the class first.
        patcher = mock.patch.object(HistoryInterface, "_load_history")
        reload = patcher.start()
        self.addCleanup(patcher.stop)
        window = MainWindow()
        self.addCleanup(window.deleteLater)
        reload.reset_mock()
        registry = shortcuts.registry
        for action in defs.all_actions():
            if action.scope == "player":
                continue
            self.assertTrue(registry.live(action.id), f"{action.id} is not bound")

        live = [s for s in registry.live("nav_search") if s.parent() is window][0]
        live.activated.emit()
        self.assertIs(window.stackedWidget.currentWidget(), window._search_page)

        history_key = [s for s in registry.live("history_reload") if s.parent() is window._history_page][0]
        history_key.activated.emit()
        reload.assert_called_once()


if __name__ == "__main__":
    unittest.main()
