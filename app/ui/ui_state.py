"""Small helpers for persistent UI geometry."""
from __future__ import annotations

import json

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QSize, Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QHeaderView,
    QHBoxLayout,
    QLayout,
    QLayoutItem,
    QListWidget,
    QListWidgetItem,
    QSplitter,
    QTableWidget,
    QVBoxLayout,
)

from qfluentwidgets import (
    BodyLabel,
    LineEdit,
    MessageBox,
    MessageBoxBase,
    PushButton,
    SubtitleLabel,
)

from ..config import app_config
from ..i18n import tr


def show_fluent_confirmation(
    parent,
    title: str,
    content: str,
    *,
    informative: str = "",
    yes_text: str | None = None,
    no_text: str | None = None,
) -> bool:
    """Show a theme-aware Fluent confirmation dialog and return whether accepted."""
    message = content
    if informative:
        message = f"{content}\n\n{informative}"
    box = MessageBox(title, message, parent)
    if yes_text:
        box.yesButton.setText(yes_text)
    if no_text:
        box.cancelButton.setText(no_text)
    return box.exec() == QDialog.DialogCode.Accepted


class _FluentTextInputDialog(MessageBoxBase):
    """Small reusable Fluent text-entry dialog."""

    def __init__(self, parent, title: str, prompt: str, text: str = ""):
        super().__init__(parent)
        self.title_label = SubtitleLabel(title, self)
        self.prompt_label = BodyLabel(prompt, self)
        self.prompt_label.setWordWrap(True)
        self.line_edit = LineEdit(self)
        self.line_edit.setText(text)
        self.line_edit.returnPressed.connect(self.yesButton.click)

        self.viewLayout.addWidget(self.title_label)
        self.viewLayout.addWidget(self.prompt_label)
        self.viewLayout.addWidget(self.line_edit)
        self.widget.setMinimumWidth(520)
        QTimer.singleShot(0, self._focus_input)

    def _focus_input(self):
        self.line_edit.setFocus()
        self.line_edit.selectAll()


def show_fluent_text_input(
    parent,
    title: str,
    prompt: str,
    *,
    text: str = "",
    accept_text: str | None = None,
    cancel_text: str | None = None,
) -> tuple[str, bool]:
    """Show a theme-aware Fluent text input and return ``(text, accepted)``."""
    box = _FluentTextInputDialog(parent, title, prompt, text)
    if accept_text:
        box.yesButton.setText(accept_text)
    if cancel_text:
        box.cancelButton.setText(cancel_text)
    accepted = box.exec() == QDialog.DialogCode.Accepted
    return box.line_edit.text(), accepted


class ResponsiveFlowLayout(QLayout):
    """Wrap controls onto rows without letting nested layouts overlap."""

    def __init__(self, parent=None, *, spacing: int = 10):
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._spacing = spacing

    def addItem(self, item: QLayoutItem):
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
        for item in self._items:
            hint = item.sizeHint()
            minimum = item.minimumSize()
            item_width = max(minimum.width(), hint.width())
            item_width = min(item_width, effective.width())
            item_height = max(minimum.height(), hint.height())
            next_x = x + item_width
            if x > effective.x() and next_x > right:
                x = effective.x()
                y += line_height + self._spacing
                next_x = x + item_width
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), QSize(item_width, item_height)))
            x = next_x + self._spacing
            line_height = max(line_height, item_height)
        return y + line_height - rect.y() + margins.bottom()


def restore_splitter_sizes(splitter: QSplitter, key: str, default_sizes: list[int]):
    raw = str(app_config.get_ui_value(key, "") or "")
    sizes = _parse_int_list(raw)
    splitter.setSizes(sizes if len(sizes) == len(default_sizes) else default_sizes)


def connect_splitter_saver(splitter: QSplitter, key: str):
    splitter.splitterMoved.connect(lambda *_args: app_config.set_ui_value(key, ",".join(str(v) for v in splitter.sizes())))


def restore_table_widths(table: QTableWidget, key: str, default_widths: dict[int, int]):
    raw = str(app_config.get_ui_value(key, "") or "")
    widths = _parse_int_list(raw)
    for col, default in default_widths.items():
        width = widths[col] if 0 <= col < len(widths) and widths[col] > 0 else default
        table.setColumnWidth(col, width)


def fit_table_last_column(table: QTableWidget):
    """Stretch the final visible column to the table's right edge.

    The table pages let users hide and reorder fields. Applying this after
    every layout/resize keeps the visible table flush with its container instead
    of leaving a large blank strip when the saved widths are narrower than the
    current window.
    """
    if table is None or table.columnCount() <= 0:
        return
    header = table.horizontalHeader()
    visible_columns = [
        column
        for column in range(table.columnCount())
        if not table.isColumnHidden(column)
    ]
    if not visible_columns:
        return
    visible_columns.sort(key=header.visualIndex)
    last_column = visible_columns[-1]
    for column in visible_columns:
        header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
    header.setSectionResizeMode(last_column, QHeaderView.ResizeMode.Stretch)


def connect_table_width_saver(table: QTableWidget, key: str):
    saver = _TableWidthSaver(table, key)
    savers = getattr(table, "_ui_state_savers", [])
    savers.append(saver)
    table._ui_state_savers = savers


def restore_table_columns(
    table: QTableWidget,
    key: str,
    *,
    default_visible: list[int] | None = None,
    default_order: list[int] | None = None,
):
    header = table.horizontalHeader()
    header.setSectionsMovable(True)
    columns = _all_columns(table)
    if default_order is None:
        default_order = columns
    if default_visible is None:
        default_visible = columns
    layout = _load_column_layout(key, columns, default_order, default_visible)
    apply_table_column_layout(
        table,
        key,
        order=layout["order"],
        visible=layout["visible"],
        sync=False,
        persist=False,
    )


def connect_table_column_saver(table: QTableWidget, key: str):
    saver = _TableColumnSaver(table, key)
    savers = getattr(table, "_ui_state_savers", [])
    savers.append(saver)
    table._ui_state_savers = savers


def open_table_column_dialog(
    table: QTableWidget,
    key: str,
    *,
    title: str,
    default_visible: list[int] | None = None,
    default_order: list[int] | None = None,
    parent=None,
):
    dlg = _TableColumnDialog(
        table,
        key,
        title=title,
        default_visible=default_visible,
        default_order=default_order,
        parent=parent or table,
    )
    dlg.exec()


def apply_table_column_layout(
    table: QTableWidget,
    key: str,
    *,
    order: list[int],
    visible: list[int],
    sync: bool,
    persist: bool = True,
):
    columns = _all_columns(table)
    valid_order = _normalize_column_order(order, columns)
    visible_set = {col for col in visible if col in columns}
    if not visible_set and columns:
        visible_set = {columns[0]}

    header = table.horizontalHeader()
    table.blockSignals(True)
    header.blockSignals(True)
    try:
        for target_visual, logical in enumerate(valid_order):
            current_visual = header.visualIndex(logical)
            if current_visual >= 0 and current_visual != target_visual:
                header.moveSection(current_visual, target_visual)
        for logical in columns:
            table.setColumnHidden(logical, logical not in visible_set)
    finally:
        header.blockSignals(False)
        table.blockSignals(False)
    if persist:
        _save_table_column_layout(table, key, sync=sync)


class _TableWidthSaver(QObject):
    def __init__(self, table: QTableWidget, key: str):
        super().__init__(table)
        self._table = table
        self._key = key
        self._sync_pending = False
        table.installEventFilter(self)
        header = table.horizontalHeader()
        header.sectionResized.connect(lambda *_args: self.save(sync=False))
        header.sectionMoved.connect(lambda *_args: self.save(sync=False))
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(lambda: self.save(sync=True))

    def eventFilter(self, watched, event):  # noqa: N802 - Qt API name
        if watched is self._table and event.type() in (
            QEvent.Type.Close,
            QEvent.Type.Hide,
            QEvent.Type.Destroy,
        ):
            self.save(sync=True)
        return super().eventFilter(watched, event)

    def save(self, *, sync: bool):
        if self._table.columnCount() <= 0:
            return
        widths = [str(self._table.columnWidth(col)) for col in range(self._table.columnCount())]
        app_config.set_ui_value(self._key, ",".join(widths), sync=sync)
        if sync:
            self._sync_pending = False
            return
        self._schedule_sync()

    def _schedule_sync(self):
        if self._sync_pending:
            return
        self._sync_pending = True

        def flush():
            self._sync_pending = False
            app_config.sync()

        QTimer.singleShot(250, flush)


class _TableColumnSaver(QObject):
    def __init__(self, table: QTableWidget, key: str):
        super().__init__(table)
        self._table = table
        self._key = key
        self._sync_pending = False
        table.installEventFilter(self)
        header = table.horizontalHeader()
        header.sectionMoved.connect(lambda *_args: self.save(sync=False))

    def eventFilter(self, watched, event):  # noqa: N802 - Qt API name
        if watched is self._table and event.type() in (
            QEvent.Type.Close,
            QEvent.Type.Hide,
            QEvent.Type.Destroy,
        ):
            self.save(sync=True)
        return super().eventFilter(watched, event)

    def save(self, *, sync: bool):
        _save_table_column_layout(self._table, self._key, sync=sync)
        if sync:
            self._sync_pending = False
            return
        self._schedule_sync()

    def _schedule_sync(self):
        if self._sync_pending:
            return
        self._sync_pending = True

        def flush():
            self._sync_pending = False
            app_config.sync()

        QTimer.singleShot(500, flush)


class _TableColumnDialog(MessageBoxBase):
    def __init__(
        self,
        table: QTableWidget,
        key: str,
        *,
        title: str,
        default_visible: list[int] | None,
        default_order: list[int] | None,
        parent=None,
    ):
        super().__init__(parent)
        self._table = table
        self._key = key
        self._default_order = default_order or _all_columns(table)
        self._default_visible = default_visible or _all_columns(table)
        self.title_label = SubtitleLabel(title, self)
        self.hint_label = BodyLabel(
            tr(
                "Check fields to show, then move them to change their order.",
                "勾选要显示的字段，并用上下按钮调整顺序。",
                "表示する列を選び、上下ボタンで順序を変更してください。",
            ),
            self,
        )
        self.hint_label.setWordWrap(True)
        self.viewLayout.addWidget(self.title_label)
        self.viewLayout.addWidget(self.hint_label)

        self._list = QListWidget(self)
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.setAlternatingRowColors(True)
        self.viewLayout.addWidget(self._list, stretch=1)

        move_row = QHBoxLayout()
        up_btn = PushButton(tr("Move Up", "上移", "上へ"), self)
        down_btn = PushButton(tr("Move Down", "下移", "下へ"), self)
        reset_btn = PushButton(tr("Recommended", "恢复推荐", "推奨に戻す"), self)
        up_btn.clicked.connect(lambda: self._move_current(-1))
        down_btn.clicked.connect(lambda: self._move_current(1))
        reset_btn.clicked.connect(self._load_defaults)
        move_row.addWidget(up_btn)
        move_row.addWidget(down_btn)
        move_row.addStretch()
        move_row.addWidget(reset_btn)
        self.viewLayout.addLayout(move_row)

        self.cancelButton.setText(tr("Cancel", "取消", "キャンセル"))
        self.yesButton.setText(tr("Save", "保存", "保存"))
        self.widget.setMinimumSize(640, 560)

        self._load_current()

    def _load_current(self):
        order = _current_column_order(self._table)
        visible = [
            col for col in _all_columns(self._table)
            if not self._table.isColumnHidden(col)
        ]
        self._populate(order, visible)

    def _load_defaults(self):
        self._populate(self._default_order, self._default_visible)

    def _populate(self, order: list[int], visible: list[int]):
        self._list.clear()
        labels = _column_labels(self._table)
        visible_set = set(visible)
        for col in _normalize_column_order(order, _all_columns(self._table)):
            label = labels.get(col, str(col))
            width = self._table.columnWidth(col)
            item = QListWidgetItem(f"{label}    {width}px")
            item.setData(Qt.ItemDataRole.UserRole, col)
            item.setToolTip(tr(f"{label}\nCurrent width: {width}px", f"{label}\n当前列宽：{width}px", f"{label}\n現在の幅: {width}px"))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if col in visible_set else Qt.CheckState.Unchecked)
            self._list.addItem(item)
        if self._list.count():
            self._list.setCurrentRow(0)

    def _move_current(self, delta: int):
        row = self._list.currentRow()
        target = row + delta
        if row < 0 or target < 0 or target >= self._list.count():
            return
        item = self._list.takeItem(row)
        self._list.insertItem(target, item)
        self._list.setCurrentRow(target)

    def validate(self) -> bool:
        """Persist the selected layout before MessageBoxBase accepts."""

        order: list[int] = []
        visible: list[int] = []
        for row in range(self._list.count()):
            item = self._list.item(row)
            col = int(item.data(Qt.ItemDataRole.UserRole))
            order.append(col)
            if item.checkState() == Qt.CheckState.Checked:
                visible.append(col)
        if not visible:
            show_fluent_confirmation(
                self,
                tr("Column Settings", "字段设置", "列設定"),
                tr("Keep at least one column visible.", "至少保留一个字段显示。", "少なくとも1列は表示してください。"),
                yes_text=tr("OK", "知道了", "OK"),
                no_text=tr("Close", "关闭", "閉じる"),
            )
            return False
        apply_table_column_layout(self._table, self._key, order=order, visible=visible, sync=True)
        return True


def _save_table_column_layout(table: QTableWidget, key: str, *, sync: bool):
    payload = {
        "order": _current_column_order(table),
        "visible": [
            col for col in _all_columns(table)
            if not table.isColumnHidden(col)
        ],
    }
    app_config.set_ui_value(f"{key}_columns", json.dumps(payload), sync=sync)


def _load_column_layout(
    key: str,
    columns: list[int],
    default_order: list[int],
    default_visible: list[int],
) -> dict[str, list[int]]:
    raw = str(app_config.get_ui_value(f"{key}_columns", "") or "")
    if raw:
        try:
            payload = json.loads(raw)
            order = _normalize_column_order(payload.get("order", []), columns)
            visible = [int(col) for col in payload.get("visible", []) if int(col) in columns]
            if visible:
                return {"order": order, "visible": visible}
        except Exception:
            pass
    return {
        "order": _normalize_column_order(default_order, columns),
        "visible": [col for col in default_visible if col in columns],
    }


def _current_column_order(table: QTableWidget) -> list[int]:
    header = table.horizontalHeader()
    pairs = []
    for logical in _all_columns(table):
        visual = header.visualIndex(logical)
        pairs.append((visual if visual >= 0 else logical, logical))
    return [logical for _, logical in sorted(pairs)]


def _normalize_column_order(order: list[int], columns: list[int]) -> list[int]:
    normalized: list[int] = []
    for raw in order:
        try:
            col = int(raw)
        except Exception:
            continue
        if col in columns and col not in normalized:
            normalized.append(col)
    normalized.extend(col for col in columns if col not in normalized)
    return normalized


def _column_labels(table: QTableWidget) -> dict[int, str]:
    labels: dict[int, str] = {}
    for col in _all_columns(table):
        item = table.horizontalHeaderItem(col)
        text = item.text().strip() if item else ""
        labels[col] = text or str(col)
    return labels


def _all_columns(table: QTableWidget) -> list[int]:
    return list(range(table.columnCount()))


def _parse_int_list(raw: str) -> list[int]:
    values: list[int] = []
    for part in str(raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            values.append(int(part))
        except ValueError:
            return []
    return values
