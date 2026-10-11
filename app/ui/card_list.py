"""Icon-mode list whose mouse wheel moves by a fraction of a card row."""

from __future__ import annotations

from PySide6.QtWidgets import QApplication, QWidget

from qfluentwidgets import ListWidget


class CardGridList(ListWidget):
    """A card grid that scrolls a little under half a row per wheel notch.

    Qt gives an icon-mode list a fixed single step, and Fluent's smooth
    scrolling multiplies each notch by 1.5 and speeds up quick turns, so one
    notch skipped most of a row and a few quick turns ran from the top of a
    page of tall cards to its bottom.  The step is derived from the grid
    height instead, and the speed-up is tamed.
    """

    WHEEL_ROW_FRACTION = 0.4
    SMOOTH_STEP_RATIO = 1.5  # qfluentwidgets' factor per notch
    ACCELERATION = 0.3  # qfluentwidgets uses 1.0: quick turns up to twice as far

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        smooth = getattr(getattr(self, "scrollDelagate", None), "verticalSmoothScroll", None)
        for engine in (
            getattr(smooth, "fixedStepScrollEngine", None),
            getattr(smooth, "adaptiveScrollEngine", None),
        ):
            if engine is not None and hasattr(engine, "acceleration"):
                engine.acceleration = self.ACCELERATION

    def wheel_step(self) -> int:
        row = self.gridSize().height() + self.spacing()
        if row <= 0:
            return 0
        lines = max(1, QApplication.wheelScrollLines())
        return max(8, round(row * self.WHEEL_ROW_FRACTION / (lines * self.SMOOTH_STEP_RATIO)))

    def updateGeometries(self):
        super().updateGeometries()  # Qt resets the single step here
        step = self.wheel_step()
        if step:
            self.verticalScrollBar().setSingleStep(step)
