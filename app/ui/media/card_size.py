"""The one poster width shared by every grid, and the slider that changes it."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QWidget

from qfluentwidgets import FluentIcon, IconWidget, Slider

from ...config import app_config
from ...i18n import tr
from ...signal_bus import signal_bus


# One poster size shared by every grid (Home, "More", subscriptions, ...).
CARD_WIDTH_KEY = "media_card_width_v1"


CARD_WIDTH_MIN = 140


CARD_WIDTH_MAX = 440


CARD_WIDTH_DEFAULT = 224


def saved_card_width() -> int:
    try:
        value = int(app_config.get_ui_value(CARD_WIDTH_KEY, CARD_WIDTH_DEFAULT) or CARD_WIDTH_DEFAULT)
    except (TypeError, ValueError):
        value = CARD_WIDTH_DEFAULT
    return max(CARD_WIDTH_MIN, min(CARD_WIDTH_MAX, value))


CARD_WIDTH_STEP = 24


def set_card_width(width: int) -> int:
    """Save and broadcast one cover size to every grid (what the slider does)."""

    width = max(CARD_WIDTH_MIN, min(CARD_WIDTH_MAX, int(width)))
    app_config.set_ui_value(CARD_WIDTH_KEY, width)
    signal_bus.media_card_size_changed.emit(width)
    return width


def step_card_width(steps: int) -> int:
    """Make the covers larger (``steps`` > 0) or smaller by one slider notch per step."""

    return set_card_width(saved_card_width() + steps * CARD_WIDTH_STEP)


def reset_card_width() -> int:
    return set_card_width(CARD_WIDTH_DEFAULT)


class CardSizeControl(QWidget):
    """Zoom slider for the poster grids; every grid follows it."""

    size_changed = Signal(int)  # the shared size changed (from this or any other slider)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        small = IconWidget(FluentIcon.ZOOM_OUT, self)
        small.setFixedSize(14, 14)
        big = IconWidget(FluentIcon.ZOOM_IN, self)
        big.setFixedSize(14, 14)
        self._slider = Slider(Qt.Orientation.Horizontal, self)
        self._slider.setRange(CARD_WIDTH_MIN, CARD_WIDTH_MAX)
        self._slider.setSingleStep(8)
        self._slider.setPageStep(24)
        self._slider.setFixedWidth(130)
        self._slider.setValue(saved_card_width())
        # Re-laying out dozens of cards on every pixel of a drag is wasted work.
        self._apply_timer = QTimer(self)
        self._apply_timer.setSingleShot(True)
        self._apply_timer.setInterval(50)
        self._apply_timer.timeout.connect(self._apply)
        self._slider.valueChanged.connect(lambda _v: self._apply_timer.start())
        self._slider.sliderReleased.connect(app_config.sync)
        self.setToolTip(tr("Cover size", "封面大小", "カバーサイズ"))
        row.addWidget(small)
        row.addWidget(self._slider)
        row.addWidget(big)
        signal_bus.media_card_size_changed.connect(self._on_broadcast)

    def _apply(self):
        value = int(self._slider.value())
        app_config.set_ui_value(CARD_WIDTH_KEY, value, sync=False)
        signal_bus.media_card_size_changed.emit(value)

    def _on_broadcast(self, value: int):
        if self._slider.value() != value:
            self._slider.blockSignals(True)
            self._slider.setValue(int(value))
            self._slider.blockSignals(False)
        self.size_changed.emit(int(value))
