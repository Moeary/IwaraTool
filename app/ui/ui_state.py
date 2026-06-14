"""Small helpers for persistent UI geometry."""
from __future__ import annotations

import json

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QVBoxLayout,
)

from ..config import app_config
from ..i18n import tr


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

        QTimer.singleShot(500, flush)


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


class _TableColumnDialog(QDialog):
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
        self.setWindowTitle(title)
        self.resize(420, 520)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        self._list = QListWidget(self)
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        root.addWidget(self._list, stretch=1)

        move_row = QHBoxLayout()
        up_btn = QPushButton(tr("Move Up", "上移", "上へ"), self)
        down_btn = QPushButton(tr("Move Down", "下移", "下へ"), self)
        reset_btn = QPushButton(tr("Recommended", "恢复推荐", "推奨に戻す"), self)
        up_btn.clicked.connect(lambda: self._move_current(-1))
        down_btn.clicked.connect(lambda: self._move_current(1))
        reset_btn.clicked.connect(self._load_defaults)
        move_row.addWidget(up_btn)
        move_row.addWidget(down_btn)
        move_row.addStretch()
        move_row.addWidget(reset_btn)
        root.addLayout(move_row)

        action_row = QHBoxLayout()
        action_row.addStretch()
        cancel_btn = QPushButton(tr("Cancel", "取消", "キャンセル"), self)
        save_btn = QPushButton(tr("Save", "保存", "保存"), self)
        cancel_btn.clicked.connect(self.reject)
        save_btn.clicked.connect(self._save_and_accept)
        action_row.addWidget(cancel_btn)
        action_row.addWidget(save_btn)
        root.addLayout(action_row)

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
            item = QListWidgetItem(labels.get(col, str(col)))
            item.setData(Qt.ItemDataRole.UserRole, col)
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

    def _save_and_accept(self):
        order: list[int] = []
        visible: list[int] = []
        for row in range(self._list.count()):
            item = self._list.item(row)
            col = int(item.data(Qt.ItemDataRole.UserRole))
            order.append(col)
            if item.checkState() == Qt.CheckState.Checked:
                visible.append(col)
        if not visible:
            QMessageBox.warning(
                self,
                tr("Column Settings", "字段设置", "列設定"),
                tr("Keep at least one column visible.", "至少保留一个字段显示。", "少なくとも1列は表示してください。"),
            )
            return
        apply_table_column_layout(self._table, self._key, order=order, visible=visible, sync=True)
        self.accept()


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
