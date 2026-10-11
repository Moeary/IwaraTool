"""Size and style of the search result grid cards."""

from __future__ import annotations

from PySide6.QtCore import QRect, QSize
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QStyle, QStyleOptionViewItem

from qfluentwidgets import ListWidget

from ..theme import palette, scrollbar_qss


def _grid_item_height(
    list_widget: ListWidget, width: int, icon_size: QSize, fallback_lines: int
) -> int:
    """Measure complete cards with the font and padding used by their delegate."""

    delegate = list_widget.itemDelegate()
    style = list_widget.style()
    base_option = QStyleOptionViewItem()
    list_widget.initViewItemOption(base_option)
    base_option.rect = QRect(0, 0, max(1, int(width)), 10000)
    height = (
        icon_size.height()
        + QFontMetrics(base_option.font).lineSpacing() * max(1, fallback_lines)
    )
    for index in range(list_widget.count()):
        item = list_widget.item(index)
        if item is None:
            continue
        option = QStyleOptionViewItem(base_option)
        delegate.initStyleOption(option, list_widget.indexFromItem(item))
        option.decorationSize = icon_size
        # The selected border is thicker. Reserve its space for every card so
        # selecting a result cannot hide the final caption line.
        option.state |= QStyle.StateFlag.State_Selected
        text_rect = style.subElementRect(
            QStyle.SubElement.SE_ItemViewItemText, option, list_widget
        )
        cover_rect = style.subElementRect(
            QStyle.SubElement.SE_ItemViewItemDecoration, option, list_widget
        )
        # Measure the painting width rather than sizeFromContents(), whose
        # wider natural size can omit a wrapped line in narrow columns. The
        # card style uses the same border/padding above and below its content.
        bottom_inset = max(0, cover_rect.top() - option.rect.top())
        height = max(height, text_rect.bottom() - option.rect.top() + 1 + bottom_inset)
    # Fluent's delegate shrinks the painting rectangle by its vertical margin.
    return height + 2 * max(0, int(getattr(delegate, "margin", 0)))


def _search_grid_style() -> str:
    p = palette()
    # Scope the shared scrollbar rules to this list's own bars.
    scrollbars = scrollbar_qss().replace("QScrollBar", "QListWidget QScrollBar")
    return f"""
        QListWidget {{
            background: transparent;
            border: none;
            outline: none;
        }}
        QListWidget::item {{
            background: {p.surface};
            border: 1px solid {p.border};
            border-radius: 8px;
            padding: 6px;
            color: {p.text};
        }}
        QListWidget::item:hover {{
            background: {p.surface};
            border: 1px solid {p.accent};
        }}
        QListWidget::item:selected,
        QListWidget::item:selected:active,
        QListWidget::item:selected:!active {{
            background: {p.selected};
            border: 2px solid {p.accent};
            color: {p.selected_text};
        }}
        {scrollbars}
    """
