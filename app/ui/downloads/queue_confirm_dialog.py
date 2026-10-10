"""Confirm a download: counts, where a file lands and what it is called, and the rule.

Top to bottom: how many videos there are, how many the site will not give
(private and similar), how many the rule stops and how many will download;
then where one random video would be saved and under which name; then the
download rule (searchable; changing it recounts at once).  The video list
with its check boxes is folded away below.  Cancel (left) keeps the selection
on the download workbench as a pending stack; Download (right) confirms.
"""

from __future__ import annotations

import os
import random
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QStringListModel, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCompleter,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    EditableComboBox,
    FluentIcon,
    MessageBoxBase,
    PushButton,
    StrongBodyLabel,
    SubtitleLabel,
    TableWidget,
    TitleLabel,
    TransparentPushButton,
    TransparentToolButton,
)

from ...core.download.listing_preview import HAVE_FILE, HAVE_HISTORY, HAVE_QUEUED, is_downloadable, preview_counts
from ...core.rules import rule_store
from ...i18n import tr
from ..rules.rule_text import _rule_display_name
from ..theme import qcolor, set_secondary_text, set_status_text


_COL_TITLE, _COL_DATE, _COL_LIKES, _COL_STATE = range(4)

OUTCOME_CONFIRM = "confirm"
OUTCOME_CANCEL = "cancel"


def _state_text(item: dict[str, Any]) -> tuple[str, str, str]:
    """``(text, tooltip, palette role)`` for the state column."""

    have, detail = str(item.get("have", "") or ""), str(item.get("have_detail", "") or "")
    if item.get("unavailable"):
        return tr("Not downloadable", "站点不可下载", "保存不可"), str(item["unavailable"]), "danger"
    if have == HAVE_FILE:
        return tr("Have file", "已有文件", "保存済み"), detail, "success"
    if have == HAVE_QUEUED:
        return tr("In task list", "已在任务列表", "タスク一覧にあり"), "", "info"
    if have == HAVE_HISTORY:
        return tr("Downloaded before (file moved)", "下载过（文件已移走）", "保存履歴あり（移動済み）"), detail, "warning"
    reason = str(item.get("match_reason", "") or "")
    if item.get("matches") is False:
        return tr("Stopped by the rule", "被规则拦住", "ルールで除外"), reason, "warning"
    if item.get("matches") is None:
        return tr("Rule checked on download", "下载时核对规则", "保存時にルール確認"), reason, "info"
    return tr("Will download", "将下载", "保存予定"), "", ""


class _StatTile(QFrame):
    """A number with a caption under it."""

    def __init__(self, caption: str, role: str, parent: QWidget):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(0)
        self.value = TitleLabel("0", self)
        if role:
            set_status_text(self.value, role)
        label = CaptionLabel(caption, self)
        set_secondary_text(label)
        layout.addWidget(self.value)
        layout.addWidget(label)
        self.setObjectName("QueueStatTile")
        self.setStyleSheet("#QueueStatTile { border: 1px solid rgba(128,128,128,0.25); border-radius: 8px; }")


class QueueConfirmDialog(MessageBoxBase):
    """Counts, a file-name example, the rule, and (folded) the checkable list."""

    def __init__(
        self,
        preview: dict[str, Any],
        *,
        reclassify: Callable[[dict[str, Any], str], dict[str, Any]] | None = None,
        from_stack: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self._reclassify = reclassify
        self._filling = False
        self.outcome = OUTCOME_CANCEL
        self.viewLayout.setSpacing(12)
        self.viewLayout.addWidget(SubtitleLabel(self._heading(preview), self))
        self.viewLayout.addLayout(self._build_stats())
        self.viewLayout.addWidget(self._build_example())
        self.viewLayout.addLayout(self._build_rule_row())
        self.viewLayout.addWidget(self._build_details(), 1)
        self._arrange_buttons(from_stack)
        self.widget.setMinimumWidth(820)
        self._set_preview(preview, keep_checks=True)

    # ── building ─────────────────────────────────────────────────────────────

    @staticmethod
    def _heading(preview: dict[str, Any]) -> str:
        label = str(preview.get("label", "") or "")
        kind = preview.get("kind")
        if kind == "playlist":
            return tr(f"Download playlist {label}", f"下载播放列表 {label}", f"プレイリスト {label} を保存")
        if kind == "user":
            return tr(f"Download author {label}", f"下载作者 {label}", f"作者 {label} を保存")
        return tr(f"Download: {label}", f"确认下载：{label}", f"保存の確認: {label}") if label else tr(
            "Confirm download", "确认下载", "保存の確認",
        )

    def _build_stats(self) -> QGridLayout:
        grid = QGridLayout()
        grid.setSpacing(8)
        self._tiles = {}
        for column, (key, caption, role) in enumerate((
            ("total", tr("videos in total", "个视频（共）", "件（合計）"), ""),
            ("unavailable", tr("not downloadable (private…)", "不能下载（隐私等）", "保存不可（非公開など）"), "danger"),
            ("blocked", tr("stopped by the rule", "被下载规则拦住", "ルールで除外"), "warning"),
            ("have", tr("already here", "已有 / 已在队列", "保存済み・キュー内"), ""),
            ("downloadable", tr("can download", "可以下载", "保存可能"), "success"),
        )):
            tile = _StatTile(caption, role, self)
            self._tiles[key] = tile
            grid.addWidget(tile, 0, column)
        return grid

    def _build_example(self) -> QWidget:
        box = QFrame(self)
        box.setObjectName("QueueExample")
        box.setStyleSheet("#QueueExample { border: 1px solid rgba(128,128,128,0.25); border-radius: 8px; }")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(12, 8, 8, 10)
        layout.setSpacing(4)
        head = QHBoxLayout()
        self._example_title = StrongBodyLabel("", box)
        self._example_title.setWordWrap(True)
        head.addWidget(self._example_title, 1)
        reroll = TransparentToolButton(FluentIcon.SYNC, box)
        reroll.setToolTip(tr("Another random video", "随机换一个", "別の動画を表示"))
        reroll.clicked.connect(self._roll_example)
        head.addWidget(reroll)
        layout.addLayout(head)
        self._example_folder = BodyLabel("", box)
        self._example_name = BodyLabel("", box)
        for label in (self._example_folder, self._example_name):
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(label)
        return box

    def _build_rule_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        label = BodyLabel(tr("Download rule", "下载规则", "ダウンロードルール"), self)
        row.addWidget(label)
        self._rules = rule_store.list_available()
        self._rule_combo = EditableComboBox(self)
        self._rule_combo.setPlaceholderText(tr("Search a rule…", "搜索下载规则…", "ルールを検索…"))
        names = []
        for rule in self._rules:
            name = _rule_display_name(rule)
            names.append(name)
            self._rule_combo.addItem(name, userData=rule["id"])
        completer = QCompleter(QStringListModel(names, self), self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self._rule_combo.setCompleter(completer)
        self._rule_combo.currentIndexChanged.connect(self._on_rule_changed)
        row.addWidget(self._rule_combo, 1)
        self._mode_note = CaptionLabel("", self)
        set_secondary_text(self._mode_note)
        row.addWidget(self._mode_note)
        return row

    def _build_details(self) -> QWidget:
        holder = QWidget(self)
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        top = QHBoxLayout()
        self._details_btn = TransparentPushButton(FluentIcon.CHEVRON_RIGHT_MED, "", holder)
        self._details_btn.clicked.connect(self._toggle_details)
        top.addWidget(self._details_btn)
        top.addStretch(1)
        self._pick_buttons = []
        for text, picker in (
            (tr("Downloadable", "勾选可下载", "保存可能を選択"), is_downloadable),
            (tr("All", "全选", "すべて"), lambda _item: True),
            (tr("None", "全不选", "選択解除"), lambda _item: False),
        ):
            button = PushButton(text, holder)
            button.clicked.connect(lambda _checked=False, pick=picker: self._check_where(pick))
            top.addWidget(button)
            self._pick_buttons.append(button)
        layout.addLayout(top)
        layout.addWidget(self._build_table(holder), 1)
        self._show_details(False)
        return holder

    def _build_table(self, parent: QWidget) -> TableWidget:
        self._table = TableWidget(parent)
        self._table.setColumnCount(4)
        self._table.setHorizontalHeaderLabels([
            tr("Title", "标题", "タイトル"), tr("Published", "发布日期", "公開日"),
            tr("Likes", "点赞", "いいね"), tr("State", "状态", "状態"),
        ])
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setWordWrap(False)
        self._table.setBorderVisible(True)
        self._table.setBorderRadius(8)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(_COL_TITLE, QHeaderView.ResizeMode.Stretch)
        for column in (_COL_DATE, _COL_LIKES, _COL_STATE):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setMinimumHeight(300)
        self._table.itemChanged.connect(self._on_item_changed)
        return self._table

    def _arrange_buttons(self, from_stack: bool):
        """Cancel on the left, Download on the right."""

        self.buttonLayout.removeWidget(self.yesButton)
        self.buttonLayout.addWidget(self.yesButton, 1, Qt.AlignmentFlag.AlignVCenter)
        self.cancelButton.setText(
            tr("Cancel", "取消", "キャンセル") if from_stack else tr(
                "Cancel (keep in Pending)", "取消（留在待下载）", "キャンセル（保留に残す）",
            )
        )
        self.cancelButton.setToolTip(tr(
            "It stays on the download workbench under Pending; open it there any time.",
            "这批作品会留在下载工作台的“待下载”里，随时可以再打开。",
            "ダウンロード画面の「保留」に残り、いつでも開き直せます。",
        ))

    # ── state ────────────────────────────────────────────────────────────────

    def _set_preview(self, preview: dict[str, Any], *, keep_checks: bool):
        self.preview = preview
        self._items: list[dict[str, Any]] = list(preview.get("items") or [])
        index = next((i for i, rule in enumerate(self._rules) if rule["id"] == preview.get("rule_id")), -1)
        if index >= 0 and index != self._rule_combo.currentIndex():
            self._rule_combo.blockSignals(True)
            self._rule_combo.setCurrentIndex(index)
            self._rule_combo.blockSignals(False)
        self._mode_note.setText(
            tr("This rule only records them as downloaded", "此规则只标记为已下载，不保存文件", "このルールは保存済みとして記録のみ")
            if preview.get("mark_only") else ""
        )
        self._fill_table(keep_checks)
        self._roll_example()

    def _on_rule_changed(self, index: int):
        rule_id = str(self._rule_combo.itemData(index) or "")
        if not rule_id or self._reclassify is None or rule_id == self.preview.get("rule_id"):
            return
        self._set_preview(self._reclassify(self.preview, rule_id), keep_checks=False)

    def _fill_table(self, keep_checks: bool):
        self._filling = True
        self._table.setUpdatesEnabled(False)
        try:
            self._table.setRowCount(len(self._items))
            for row, item in enumerate(self._items):
                checked = item.get("checked", is_downloadable(item)) if keep_checks else is_downloadable(item)
                self._fill_row(row, item, bool(checked))
        finally:
            self._table.setUpdatesEnabled(True)
            self._filling = False
        self._refresh_counts()

    def _fill_row(self, row: int, item: dict[str, Any], checked: bool):
        title = QTableWidgetItem(str(item.get("title", "") or item.get("video_id", "")))
        title.setFlags(title.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        title.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
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

    def checked_ids(self) -> list[str]:
        return [str(item["video_id"]) for item in self.checked_items()]

    def _refresh_counts(self):
        for key, value in preview_counts(self._items).items():
            self._tiles[key].value.setText(str(value))
        reasons = sorted({str(i["unavailable"]) for i in self._items if i.get("unavailable")})
        self._tiles["unavailable"].setToolTip("\n".join(reasons[:6]))
        chosen = [item for item in self.checked_items() if is_downloadable(item) or item.get("have") == HAVE_HISTORY]
        count = len(self.checked_items())
        if self.preview.get("mark_only"):
            self.yesButton.setText(tr(f"Mark {count} as downloaded", f"确认标记（{count}）", f"{count} 件を記録"))
            self.yesButton.setEnabled(count > 0)
        else:
            # Checked videos that are already here are skipped by the queue anyway.
            self.yesButton.setText(tr(f"Download ({len(chosen)})", f"确认下载（{len(chosen)}）", f"保存する（{len(chosen)}）"))
            self.yesButton.setEnabled(bool(chosen))
        self._details_btn.setText(tr(
            f"Videos and check boxes ({count} checked, {len(chosen)} will download)",
            f"查看作品明细并勾选（已勾选 {count}，其中将下载 {len(chosen)}）",
            f"動画一覧と選択（選択 {count}、保存予定 {len(chosen)}）",
        ))

    def _roll_example(self):
        pool = [item for item in self.checked_items() if is_downloadable(item)] or [
            item for item in self._items if is_downloadable(item)
        ] or self._items
        item = random.choice(pool) if pool else {}
        relative = str(item.get("relative_path", "") or "")
        if not relative:
            self._example_title.setText(tr("No video to show", "没有可展示的视频", "表示できる動画がありません"))
            self._example_folder.setText("")
            self._example_name.setText("")
            return
        full = os.path.join(str(self.preview.get("save_dir", "") or ""), relative)
        self._example_title.setText(tr(
            f"For example (random): {item.get('title') or item.get('video_id')}",
            f"示例（随机）：{item.get('title') or item.get('video_id')}",
            f"例（ランダム）: {item.get('title') or item.get('video_id')}",
        ))
        self._example_folder.setText(tr(f"Saved in: {os.path.dirname(full)}", f"保存到：{os.path.dirname(full)}", f"保存先: {os.path.dirname(full)}"))
        self._example_name.setText(tr(f"Named: {os.path.basename(full)}", f"重命名为：{os.path.basename(full)}", f"ファイル名: {os.path.basename(full)}"))

    def _toggle_details(self):
        self._show_details(self._table.isHidden())

    def _show_details(self, shown: bool):
        self._table.setVisible(shown)
        for button in self._pick_buttons:
            button.setVisible(shown)
        self._details_btn.setIcon(FluentIcon.CHEVRON_DOWN_MED if shown else FluentIcon.CHEVRON_RIGHT_MED)

    def ask(self) -> str:
        """Show the dialog; OUTCOME_CONFIRM or OUTCOME_CANCEL (``preview`` / ``checked_ids()`` stay readable)."""

        self.outcome = OUTCOME_CONFIRM if self.exec() == QDialog.DialogCode.Accepted else OUTCOME_CANCEL
        return self.outcome
