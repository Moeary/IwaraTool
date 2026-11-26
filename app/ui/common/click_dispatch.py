"""Single- and double-click actions for result cards (see ``presentation.click_actions``)."""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QApplication

from ...presentation.click_actions import (  # noqa: F401 - re-exported
    ACTION_DETAIL,
    ACTION_NONE,
    ACTION_PLAY,
    ACTION_SELECT,
    ACTIONS,
    ClickDecider,
    action_label,
    click_action,
    double_click_action,
    playable_video_id,
)


def play_in_window(video) -> bool:
    """Ask for the player window for an Iwara video; False when there is nothing to play."""

    from ...signal_bus import signal_bus

    video_id = playable_video_id(video)
    if not video_id:
        return False
    signal_bus.video_preview_requested.emit(video_id, str(getattr(video, "title", "") or ""), "")
    return True


class ClickDispatcher(QObject):
    """Calls ``perform(action, target)`` for clicks and double clicks on ``target``."""

    def __init__(self, perform: Callable[[str, object], None], parent: QObject | None = None):
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._decider = ClickDecider(
            perform,
            start_timer=lambda: self._timer.start(QApplication.doubleClickInterval()),
            stop_timer=self._timer.stop,
            single=lambda: click_action(),
            double=lambda: double_click_action(),
        )
        self._timer.timeout.connect(self._decider.timer_fired)

    def click(self, target):
        self._decider.click(target)

    def double_click(self, target):
        self._decider.double_click(target)
