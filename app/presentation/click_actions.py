"""Single- and double-click actions for result cards, chosen in Settings.

A click can *select* the item (add it to the candidates the action buttons
work on), open its *detail* page, *play* it in the player window, or do
nothing.  ``ClickDecider`` turns raw clicks into those actions without a UI
framework; the interface supplies the timer:

* a click whose action is "select" happens at once, and a double click undoes
  it before running its own action, so double-clicking never leaves the item
  toggled;
* any other click action waits out the double-click interval when a double
  click could still follow, so one double click never also counts as a click.
"""
from __future__ import annotations

from typing import Callable

from ..config import app_config
from ..core.search.models import IWARA_IMAGE_SOURCE_KIND
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


def playable_video_id(video) -> str:
    """The Iwara video ID the player window can play for a card, ``""`` when there is none."""

    if getattr(video, "source_kind", "") == IWARA_IMAGE_SOURCE_KIND or not getattr(video, "downloadable", True):
        return ""
    return str(getattr(video, "video_id", "") or "")


class ClickDecider:
    """Calls ``perform(action, target)`` for clicks; ``start_timer()`` / ``stop_timer()`` are the interface's.

    When a click must wait for a possible double click, the decider calls
    ``start_timer()``; the interface calls ``timer_fired()`` once the
    double-click interval has passed.
    """

    def __init__(
        self,
        perform: Callable[[str, object], None],
        *,
        start_timer: Callable[[], None],
        stop_timer: Callable[[], None],
        single: Callable[[], str] = click_action,
        double: Callable[[], str] = double_click_action,
    ):
        self._perform = perform
        self._start_timer = start_timer
        self._stop_timer = stop_timer
        self._single = single
        self._double = double
        self._selected = None  # the target the last click selected (to undo on a double click)
        self._pending = None  # a delayed click action: (action, target)

    def click(self, target) -> None:
        single, double = self._single(), self._double()
        self._stop_timer()
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
            self._start_timer()

    def double_click(self, target) -> None:
        self._stop_timer()
        self._pending = None
        if self._selected is not None and self._selected is target:
            self._perform(ACTION_SELECT, target)  # undo the first click's toggle
        self._selected = None
        action = self._double()
        if action != ACTION_NONE:
            self._perform(action, target)

    def timer_fired(self) -> None:
        pending, self._pending = self._pending, None
        if pending is not None:
            self._perform(*pending)
