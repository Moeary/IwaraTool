"""Every Flet grid follows one cover width: the slider and Ctrl+= / Ctrl+- / Ctrl+0."""
from __future__ import annotations

import asyncio
import importlib.util
import unittest
from types import SimpleNamespace
from unittest import mock

HAS_FLET = importlib.util.find_spec("flet") is not None


@unittest.skipUnless(HAS_FLET, "Flet is not installed in this environment")
class CoverSizeTests(unittest.TestCase):
    def test_shortcuts_step_reset_and_save(self):
        from app.presentation.media import card_size
        from app.ui_flet.shell import Shell

        saved = []
        shell = SimpleNamespace(state=SimpleNamespace(cover_size=card_size.CARD_WIDTH_DEFAULT))
        shell.set_cover_size = lambda width, save=True: Shell.set_cover_size(shell, width, save=save)
        with mock.patch.object(card_size, "save_card_width", lambda w, **_k: saved.append(w) or w):
            asyncio.run(Shell.run_action(shell, "card_size_up"))
            self.assertEqual(shell.state.cover_size, card_size.CARD_WIDTH_DEFAULT + card_size.CARD_WIDTH_STEP)
            asyncio.run(Shell.run_action(shell, "card_size_reset"))
            self.assertEqual(shell.state.cover_size, card_size.CARD_WIDTH_DEFAULT)
            shell.set_cover_size(300.4, save=False)  # dragging: not saved yet
        self.assertEqual(shell.state.cover_size, 300)
        self.assertEqual(saved, [card_size.CARD_WIDTH_DEFAULT + card_size.CARD_WIDTH_STEP, card_size.CARD_WIDTH_DEFAULT])

    def test_phones_keep_adaptive_columns(self):
        from app.ui_flet.components.video_grid import cover_size_for

        state = SimpleNamespace(cover_size=300)
        self.assertEqual(cover_size_for(SimpleNamespace(state=state, caps=SimpleNamespace(desktop=True)), 0.5), 150)
        self.assertIsNone(cover_size_for(SimpleNamespace(state=state, caps=SimpleNamespace(desktop=False))))


if __name__ == "__main__":
    unittest.main()
