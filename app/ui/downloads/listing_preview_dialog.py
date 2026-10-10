"""Show what an author / playlist link contains and let the user pick what to queue."""

from __future__ import annotations

import os
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QTableWidgetItem

from qfluentwidgets import BodyLabel, CaptionLabel, MessageBoxBase, PushButton, SubtitleLabel, TableWidget

from ...core.download.listing_preview import HAVE_FILE, HAVE_HISTORY, HAVE_QUEUED
from ...i18n import tr
from ..theme import qcolor, set_secondary_text, summary_text


_COL_TITLE, _COL_DATE, _COL_LIKES, _COL_STATE = range(4)


def suggested(item: dict[str, Any]) -> bool:
    """Checked by default: not here yet in any form, and the rule keeps it."""

    return not item.get("have") and bool(item.get("matches"))


def _state_text(item: dict[str, Any]) -> tuple[str, str, str]:
    """``(text, tooltip, palette role)`` for the state column."""

    have, detail = str(item.get("have", "") or ""), str(item.get("have_detail", "") or "")
    if have == HAVE_FILE:
        return tr("Have file", "已有文件", "保存済み"), detail, "success"
    if have == HAVE_QUEUED:
        return tr("In task list", "已在任务列表", "タスク一覧にあり"), "", "info"
    if have == HAVE_HISTORY:
        return (
            tr("Downloaded before (file moved)", "下载过（文件已移走）", "保存履歴あり（移動済み）"),
            detail,
            "warning",
        )
    if not item.get("matches"):
        reason = str(item.get("match_reason", "") or "")
        return tr("Rule filters it out", "不符合规则", "ルール対象外"), reason, "warning"
    return tr("New", "可下载", "新規"), "", ""


class ListingPreviewDialog(MessageBoxBase):
    """Counts, a checkable list, the target folder and a file-name example."""

    def __init__(self, preview: dict[str, Any], *, mark_only: bool = False, parent=None):
        super().__init__(parent)
        self._items: list[dict[str, Any]] = list(preview.get("items") or [])
        self._preview = preview
        self._mark_only = mark_only
        self._filling = False
        self.viewLayout.addWidget(SubtitleLabel(self._heading(), self))
        self._summary = BodyLabel("", self)
        self._summary.setWordWrap(True)
        self.viewLayout.addWidget(self._summary)
        self.viewLayout.addLayout(self._build_select_row())
        self.viewLayout.addWidget(self._build_table(), 1)
        self.viewLayout.addWidget(self._build_target_labels()[0])
        self.viewLayout.addWidget(self._example)
        self.cancelButton.setText(tr("Cancel", "取消", "キャンセル"))
        self.widget.setMinimumSize(920, 620)
        self._fill_table()

    def _heading(self) -> str:
        label = str(self._preview.get("label", "") or "")
        if self._preview.get("kind") == "playlist":
            return tr(f"Playlist {label}", f"播放列表 {label}", f"プレイリスト {label}")
        return tr(f"Author {label}", f"作者 {label}", f"作者 {label}")

    def _build_select_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        for text, tip, picker in (
            (
                tr("Suggested", "勾选建议项", "推奨を選択"),
                tr("New videos the rule keeps", "未下载且符合规则的作品", "未保存かつルールに合う動画"),
                suggested,
            ),
            (tr("All", "全选", "すべて"), "", lambda _item: True),
            (tr("None", "全不选", "選択解除"), "", lambda _item: False),
        ):
            button = PushButton(text, self)
            if tip:
                button.setToolTip(tip)
            button.clicked.connect(lambda _checked=False, pick=picker: self._check_where(pick))
            row.addWidget(button)
        row.addStretch(1)
        return row

    def _build_table(self) -> TableWidget:
        self._table = TableWidget(self)
        self._table.setColumnCount(4)
        self._table.setHorizontalHeaderLabels([
            tr("Title", "标题", "タイトル"),
            tr("Published", "发布日期", "公開日"),
            tr("Likes", "点赞", "いいね"),
            tr("State", "状态", "状態"),
        ])
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setWordWrap(False)
        self._table.setBorderVisible(True)
        self._table.setBorderRadius(8)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(_COL_TITLE, QHeaderView.ResizeMode.Stretch)
        for column, width in ((_COL_DATE, 124), (_COL_LIKES, 80), (_COL_STATE, 190)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
            self._table.setColumnWidth(column, width)
        self._table.setMinimumHeight(340)
        self._table.itemChanged.connect(self._on_item_changed)
        self._table.currentCellChanged.connect(lambda *_args: self._update_example())
        return self._table

    def _build_target_labels(self):
        save_dir = str(self._preview.get("save_dir", "") or "")
        self._target = CaptionLabel(tr(f"Save to: {save_dir}", f"保存目录：{save_dir}", f"保存先: {save_dir}"), self)
        self._target.setWordWrap(True)
        self._target.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        set_secondary_text(self._target)
        self._example = CaptionLabel("", self)
        self._example.setWordWrap(True)
        self._example.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        set_secondary_text(self._example)
        return self._target, self._example

    def _fill_table(self):
        self._filling = True
        self._table.setUpdatesEnabled(False)
        try:
            self._table.setRowCount(len(self._items))
            for row, item in enumerate(self._items):
                self._fill_row(row, item)
        finally:
            self._table.setUpdatesEnabled(True)
            self._filling = False
        self._refresh_counts()

    def _fill_row(self, row: int, item: dict[str, Any]):
        title = QTableWidgetItem(str(item.get("title", "") or item.get("video_id", "")))
        title.setFlags(title.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        title.setCheckState(Qt.CheckState.Checked if suggested(item) else Qt.CheckState.Unchecked)
        title.setToolTip(str(item.get("video_id", "")))
        self._table.setItem(row, _COL_TITLE, title)
        self._table.setItem(row, _COL_DATE, QTableWidgetItem(str(item.get("published_at", "") or "")[:10]))
        likes = QTableWidgetItem(str(item.get("likes", 0) or 0))
        likes.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._table.setItem(row, _COL_LIKES, likes)
        text, tooltip, role = _state_text(item)
        state = QTableWidgetItem(text)
        if tooltip:
            state.setToolTip(tooltip)
        if role:
            state.setForeground(qcolor(role))
        self._table.setItem(row, _COL_STATE, state)

    def _check_where(self, pick):
        self._filling = True
        try:
            for row, item in enumerate(self._items):
                cell = self._table.item(row, _COL_TITLE)
                if cell is not None:
                    cell.setCheckState(Qt.CheckState.Checked if pick(item) else Qt.CheckState.Unchecked)
        finally:
            self._filling = False
        self._refresh_counts()

    def _on_item_changed(self, item: QTableWidgetItem):
        if not self._filling and item.column() == _COL_TITLE:
            self._refresh_counts()

    def checked_items(self) -> list[dict[str, Any]]:
        return [
            item
            for row, item in enumerate(self._items)
            if (cell := self._table.item(row, _COL_TITLE)) is not None and cell.checkState() == Qt.CheckState.Checked
        ]

    def _refresh_counts(self):
        have = sum(1 for item in self._items if item.get("have"))
        matching = sum(1 for item in self._items if item.get("matches"))
        chosen = len(self.checked_items())
        rule_name = str(self._preview.get("rule_name", "") or "")
        self._summary.setText(summary_text([
            (tr("Found", "发现作品", "検出"), len(self._items)),
            (tr("Already here", "已有", "保存済み等"), have),
            (tr(f"Match rule \"{rule_name}\"", f"符合规则“{rule_name}”", f"ルール「{rule_name}」に一致"), matching),
            (tr("Checked", "已勾选", "選択中"), chosen),
        ]))
        if self._mark_only:
            text = tr(f"Mark {chosen} as downloaded", f"标记 {chosen} 个为已下载", f"{chosen} 件を保存済みにする")
        else:
            text = tr(f"Queue {chosen}", f"加入队列（{chosen}）", f"{chosen} 件をキューへ")
        self.yesButton.setText(text)
        self.yesButton.setEnabled(chosen > 0)
        self._update_example()

    def _update_example(self):
        row = self._table.currentRow()
        if 0 <= row < len(self._items):
            item = self._items[row]
        else:
            chosen = self.checked_items()
            item = chosen[0] if chosen else (self._items[0] if self._items else {})
        relative = str(item.get("relative_path", "") or "")
        if not relative:
            self._example.setText("")
            return
        full = os.path.join(str(self._preview.get("save_dir", "") or ""), relative)
        self._example.setText(tr(f"File name example: {full}", f"文件名示例：{full}", f"ファイル名の例: {full}"))

    def choose(self) -> list[dict[str, Any]]:
        """Show the dialog; the checked videos, or an empty list when cancelled."""

        if self.exec() != QDialog.DialogCode.Accepted:
            return []
        return self.checked_items()
