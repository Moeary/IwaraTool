"""Single- and double-click actions for result cards, chosen in Settings.

A click can *select* the item (add it to the candidates the action buttons
work on), open its *detail* page, *play* it in the player window, or do
nothing.  ``ClickDispatcher`` turns raw clicks into those actions:

* a click whose action is "select" happens at once, and a double click undoes
  it before running its own action, so double-clicking never leaves the item
  toggled;
* any other click action waits out the double-click interval when a double
  click could still follow, so one double click never also counts as a click.
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QApplication

from ..config import app_config
from ..i18n import tr

ACTION_SELECT = "select"
ACTION_DETAIL = "detail"
ACTION_PLAY = "play"
ACTION_NONE = "none"
ACTIONS = (ACTION_SELECT, ACTION_DETAIL, ACTION_PLAY, ACTION_NONE)


def action_label(action: str) -> str:
    return {
        ACTION_SELECT: tr("Add to the selection", "加入操作候选（选中）", "選択に追加"),
        ACTION_DETAIL: tr("Open the detail page", "打开详情页", "詳細ページを開く"),
        ACTION_PLAY: tr("Play in the player window", "在播放窗口中播放", "プレーヤーで再生"),
        ACTION_NONE: tr("Do nothing", "无操作", "何もしない"),
    }.get(action, action)


def click_action() -> str:
    return app_config.media_click_action


def double_click_action() -> str:
    return app_config.media_double_click_action


def play_in_window(video) -> bool:
    """Ask for the player window for an Iwara video; False when there is nothing to play."""

    from ..core.search import IWARA_IMAGE_SOURCE_KIND
    from ..signal_bus import signal_bus

    if getattr(video, "source_kind", "") == IWARA_IMAGE_SOURCE_KIND or not getattr(video, "downloadable", True):
        return False
    video_id = str(getattr(video, "video_id", "") or "")
    if not video_id:
        return False
    signal_bus.video_preview_requested.emit(video_id, str(getattr(video, "title", "") or ""), "")
    return True


class ClickDispatcher(QObject):
    """Calls ``perform(action, target)`` for clicks and double clicks on ``target``."""

    def __init__(self, perform: Callable[[str, object], None], parent: QObject | None = None):
        super().__init__(parent)
        self._perform = perform
        self._selected = None  # the target the last click selected (to undo on a double click)
        self._pending = None  # a delayed click action: (action, target)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fire_pending)

    def click(self, target):
        single, double = click_action(), double_click_action()
        self._timer.stop()
        self._pending = None
        self._selected = None
        if single == ACTION_NONE:
            return
        if single == ACTION_SELECT:
            self._selected = target
            self._perform(ACTION_SELECT, target)
        elif double == ACTION_NONE:
            self._perform(single, target)
        else:
            self._pending = (single, target)
            self._timer.start(QApplication.doubleClickInterval())

    def double_click(self, target):
        self._timer.stop()
        self._pending = None
        if self._selected is not None and self._selected is target:
            self._perform(ACTION_SELECT, target)  # undo the first click's toggle
        self._selected = None
        action = double_click_action()
        if action != ACTION_NONE:
            self._perform(action, target)

    def _fire_pending(self):
        pending, self._pending = self._pending, None
        if pending is not None:
            self._perform(*pending)
