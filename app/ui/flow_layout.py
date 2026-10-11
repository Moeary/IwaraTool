"""A layout that wraps its widgets onto new rows as the width shrinks."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QLayout, QLayoutItem


class ResponsiveFlowLayout(QLayout):
    """Wrap controls onto rows without letting nested layouts overlap."""

    def __init__(self, parent=None, *, spacing: int = 10):
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._spacing = spacing

    def addItem(self, item: QLayoutItem):
        widget = item.widget()
        if widget is not None:
            # Cocoa buttons have extra style margins; align the actual widget
            # rectangles like the other platforms, including fixed heights.
            widget.setAttribute(Qt.WidgetAttribute.WA_LayoutUsesWidgetRect, True)
        self._items.append(item)
        self.invalidate()

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> QLayoutItem | None:
        item = self._items.pop(index) if 0 <= index < len(self._items) else None
        if item is not None:
            self.invalidate()
        return item

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, max(0, width), 0), test_only=True)

    def setGeometry(self, rect: QRect):
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:
        width = self._layout_width_hint()
        return QSize(width, self.heightForWidth(width))

    def minimumSize(self) -> QSize:
        # QVBoxLayout asks child layouts for a minimum height before it has
        # assigned their final width.  Returning only the tallest child (the
        # old behaviour) made later wrapped rows paint on top of each other.
        width = self._layout_width_hint()
        margins = self.contentsMargins()
        height = self.heightForWidth(width)
        min_width = max((item.minimumSize().width() for item in self._items), default=0)
        return QSize(
            min_width,
            max(height, margins.top() + margins.bottom()),
        )

    def _layout_width_hint(self) -> int:
        margins = self.contentsMargins()
        parent = self.parentWidget()
        if self.geometry().width() > 0:
            width = self.geometry().width()
        elif parent is not None and parent.width() > 0:
            width = parent.width()
        else:
            width = sum(max(item.sizeHint().width(), item.minimumSize().width()) for item in self._items)
            width += max(0, len(self._items) - 1) * self._spacing
        return max(1, width - margins.left() - margins.right())

    def _do_layout(self, rect: QRect, *, test_only: bool) -> int:
        margins = self.contentsMargins()
        effective = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        if effective.width() <= 0 or not self._items:
            return margins.top() + margins.bottom()

        x = effective.x()
        y = effective.y()
        line_height = 0
        right = effective.right()
        # Items are placed per line so shorter controls (labels, switches) can
        # be centred against taller buttons on the same row.
        line: list[tuple[QLayoutItem, int, int, int]] = []

        def flush_line():
            if test_only:
                return
            for line_item, item_x, width, height in line:
                offset = max(0, (line_height - height) // 2)
                line_item.setGeometry(QRect(QPoint(item_x, y + offset), QSize(width, height)))

        for item in self._items:
            hint = item.sizeHint()
            minimum = item.minimumSize()
            item_width = max(minimum.width(), hint.width())
            item_width = min(item_width, effective.width())
            item_height = max(minimum.height(), hint.height())
            next_x = x + item_width
            if x > effective.x() and next_x > right:
                flush_line()
                line = []
                x = effective.x()
                y += line_height + self._spacing
                next_x = x + item_width
                line_height = 0
            line.append((item, x, item_width, item_height))
            x = next_x + self._spacing
            line_height = max(line_height, item_height)
        flush_line()
        return y + line_height - rect.y() + margins.bottom()
