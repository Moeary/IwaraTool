"""Dialog for choosing and ordering a table's visible columns."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView, QHBoxLayout, QListWidgetItem, QTableWidget

from qfluentwidgets import BodyLabel, ListWidget, MessageBoxBase, PushButton, SubtitleLabel

from ..i18n import tr
from .fluent_dialogs import show_fluent_confirmation
from .ui_state import (
    _all_columns,
    _column_labels,
    _current_column_order,
    _normalize_column_order,
    apply_table_column_layout,
)


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

        self._list = ListWidget(self)
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
