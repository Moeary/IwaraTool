"""The one cover (poster) width shared by every grid, in both front ends.

The value is a grid's minimum card width; the slider, ``Ctrl+=`` / ``Ctrl+-`` /
``Ctrl+0`` and every grid read and write the same saved setting.  Each front
end broadcasts a change in its own way.
"""
from __future__ import annotations

from ...config import app_config

CARD_WIDTH_KEY = "media_card_width_v1"
CARD_WIDTH_MIN = 140
CARD_WIDTH_MAX = 440
CARD_WIDTH_DEFAULT = 224
CARD_WIDTH_STEP = 24  # one slider notch / shortcut press
PANE_SCALE = 0.8  # a list-detail pane (a subscription's works) shows the covers a little smaller


def clamp_card_width(width) -> int:
    try:
        value = int(width)
    except (TypeError, ValueError):
        value = CARD_WIDTH_DEFAULT
    return max(CARD_WIDTH_MIN, min(CARD_WIDTH_MAX, value))


def saved_card_width() -> int:
    return clamp_card_width(app_config.get_ui_value(CARD_WIDTH_KEY, CARD_WIDTH_DEFAULT) or CARD_WIDTH_DEFAULT)


def save_card_width(width, *, sync: bool = True) -> int:
    width = clamp_card_width(width)
    app_config.set_ui_value(CARD_WIDTH_KEY, width, sync=sync)
    return width


def stepped_card_width(current: int, steps: int) -> int:
    return clamp_card_width(current + steps * CARD_WIDTH_STEP)


def cover_size_label() -> str:
    from ...i18n import tr

    return tr("Cover size", "封面大小", "カバーサイズ")
