"""The workbench's confirmation panel for videos waiting to be downloaded.

Sits above the task list while anything waits: the batch, how many can
download / are skipped / cannot download (each one filters the list), the
rule (searchable; changing it re-checks the batch at once), where a random
video would be saved and what it would be called, then Clear (left) and
Confirm download (right).
"""

from __future__ import annotations

import os

from PySide6.QtCore import QStringListModel, Qt, QTimer, Signal
from PySide6.QtWidgets import QCompleter, QFrame, QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    ComboBox,
    EditableComboBox,
    FluentIcon,
    InfoBar,
    InfoBarPosition,
    PrimaryPushButton,
    PushButton,
    SubtitleLabel,
    TitleLabel,
    TransparentToolButton,
)

from ...config import app_config
from ...core.download.staging import STAGE_READY, STAGE_SKIP, STAGE_UNAVAILABLE
from ...core.rules import rule_store
from ...i18n import tr
from ...signal_bus import signal_bus
from ..fluent_dialogs import show_fluent_confirmation
from ..rules.rule_text import _rule_display_name
from ..theme import CARD_MARGINS, set_secondary_text, set_status_text
from .page_dependencies import download_manager


class _CountTile(QFrame):
    """A big number with a caption; a click filters the task list."""

    clicked = Signal()

    def __init__(self, caption: str, role: str, tooltip: str, parent: QWidget):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tooltip)
        self.setObjectName("StagingTile")
        self.setStyleSheet("#StagingTile { border: 1px solid rgba(128,128,128,0.25); border-radius: 8px; }")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(0)
        self.value = TitleLabel("0", self)
        set_status_text(self.value, role)
        label = CaptionLabel(caption, self)
        set_secondary_text(label)
        layout.addWidget(self.value)
        layout.addWidget(label)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()


class StagingPanel(CardWidget):
    """Confirm (or clear) the videos waiting on the workbench, one batch or all."""

    show_requested = Signal(str, str)  # verdict ("" = all), batch id ("" = all)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._syncing = False
        self._last_total = 0  # more waiting than last time: show them in the list
        root = QVBoxLayout(self)
        root.setContentsMargins(*CARD_MARGINS)
        root.setSpacing(10)
        root.addLayout(self._build_header())
        root.addLayout(self._build_tiles())
        root.addLayout(self._build_rule_row())
        root.addLayout(self._build_example())
        root.addLayout(self._build_buttons())
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(80)
        self._refresh_timer.timeout.connect(self.refresh)
        signal_bus.staging_changed.connect(self._refresh_timer.start)
        signal_bus.rules_changed.connect(self._reload_rules)
        self.refresh()

    # ── building ─────────────────────────────────────────────────────────────

    def _build_header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self._title = SubtitleLabel("", self)
        row.addWidget(self._title)
        row.addStretch(1)
        self._batch_combo = ComboBox(self)
        self._batch_combo.setMinimumWidth(260)
        self._batch_combo.currentIndexChanged.connect(self._on_batch_changed)
        row.addWidget(self._batch_combo)
        return row

    def _build_tiles(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        self._tiles: dict[str, _CountTile] = {}
        for verdict, caption, role, tip in (
            (STAGE_READY, tr("can download", "可以下载", "保存可能"), "success",
             tr("Show the videos that will download", "只看将要下载的作品", "保存予定のみ表示")),
            (STAGE_SKIP, tr("skipped (already here / rule)", "跳过（已有 / 被规则拦住）", "スキップ（保存済み・ルール）"), "warning",
             tr("Already here, or stopped by the rule; right-click to allow", "已有文件、已在队列或被规则拦住；右键可放行", "保存済み・ルール除外。右クリックで許可")),
            (STAGE_UNAVAILABLE, tr("cannot download (private…)", "不能下（私有等）", "保存不可（非公開など）"), "danger",
             tr("The site does not offer the file", "站点不提供文件（私有、外链等）", "サイトがファイルを提供していません")),
        ):
            tile = _CountTile(caption, role, tip, self)
            tile.clicked.connect(lambda verdict=verdict: self.show_requested.emit(verdict, self._batch_id()))
            self._tiles[verdict] = tile
            row.addWidget(tile, 1)
        return row

    def _build_rule_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(BodyLabel(tr("Download rule", "下载规则", "ダウンロードルール"), self))
        self._rule_combo = EditableComboBox(self)
        self._rule_combo.setPlaceholderText(tr("Search a rule…", "搜索下载规则…", "ルールを検索…"))
        self._rule_combo.currentIndexChanged.connect(self._on_rule_changed)
        row.addWidget(self._rule_combo, 1)
        self._reload_rules()
        return row

    def _build_example(self) -> QHBoxLayout:
        row = QHBoxLayout()
        column = QVBoxLayout()
        column.setSpacing(2)
        self._example_title = CaptionLabel("", self)
        set_secondary_text(self._example_title)
        self._example_folder = BodyLabel("", self)
        self._example_name = BodyLabel("", self)
        for label in (self._example_title, self._example_folder, self._example_name):
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            column.addWidget(label)
        row.addLayout(column, 1)
        reroll = TransparentToolButton(FluentIcon.SYNC, self)
        reroll.setToolTip(tr("Another random video", "随机换一个", "別の動画を表示"))
        reroll.clicked.connect(self._show_example)
        row.addWidget(reroll, 0, Qt.AlignmentFlag.AlignTop)
        return row

    def _build_buttons(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        self._clear_btn = PushButton(tr("Clear", "清空待确认", "クリア"), self, FluentIcon.DELETE)
        self._clear_btn.clicked.connect(self._clear)
        self._confirm_btn = PrimaryPushButton("", self, FluentIcon.DOWNLOAD)
        self._confirm_btn.clicked.connect(self._confirm)
        row.addWidget(self._clear_btn, 1)
        row.addWidget(self._confirm_btn, 1)
        return row

    # ── state ────────────────────────────────────────────────────────────────

    def _batch_id(self) -> str:
        return str(self._batch_combo.currentData() or "")

    def _reload_rules(self):
        self._rules = rule_store.list_available()
        self._syncing = True
        self._rule_combo.clear()
        names = []
        for rule in self._rules:
            names.append(_rule_display_name(rule))
            self._rule_combo.addItem(names[-1], userData=rule["id"])
        completer = QCompleter(QStringListModel(names, self), self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self._rule_combo.setCompleter(completer)
        self._syncing = False
        if hasattr(self, "_batch_combo"):
            self.refresh()

    def refresh(self):
        batches = download_manager.staged_batches()
        total = sum(batch["total"] for batch in batches)
        grew, self._last_total = total > self._last_total, total
        self.setVisible(bool(batches))
        if not batches:
            return
        if grew:
            QTimer.singleShot(0, lambda: self.show_requested.emit("", self._batch_id()))
        self._fill_batches(batches, total)
        self._title.setText(tr(f"To confirm ({total})", f"待确认（{total}）", f"確認待ち（{total}）"))
        current = self._batch_id()
        counts = download_manager.staged_counts(current)
        for verdict, tile in self._tiles.items():
            tile.value.setText(str(counts[verdict]))
        rule_id = next((b["rule_id"] for b in batches if not current or b["batch_id"] == current), "")
        self._select_rule(rule_id)
        ready = counts[STAGE_READY]
        self._confirm_btn.setText(tr(f"Confirm download ({ready})", f"确认下载（{ready}）", f"保存する（{ready}）"))
        self._confirm_btn.setEnabled(ready > 0)
        self._show_example()

    def _fill_batches(self, batches: list[dict], total: int):
        current = self._batch_id()
        self._syncing = True
        self._batch_combo.blockSignals(True)
        self._batch_combo.clear()
        self._batch_combo.addItem(tr(f"All batches · {total}", f"全部批次 · {total}", f"すべて · {total}"), userData="")
        for batch in batches:
            self._batch_combo.addItem(f"{batch['label']} · {batch['total']}", userData=batch["batch_id"])
        index = next((i for i in range(self._batch_combo.count()) if self._batch_combo.itemData(i) == current), 0)
        self._batch_combo.setCurrentIndex(index)
        self._batch_combo.blockSignals(False)
        self._syncing = False

    def _select_rule(self, rule_id: str):
        index = next((i for i, rule in enumerate(self._rules) if rule["id"] == rule_id), -1)
        if index >= 0 and index != self._rule_combo.currentIndex():
            self._syncing = True
            self._rule_combo.setCurrentIndex(index)
            self._syncing = False

    def _show_example(self):
        example = download_manager.staged_example(self._batch_id())
        if not example:
            self._example_title.setText(tr("Nothing here would download yet.", "这里还没有将要下载的作品。", "保存予定の動画はまだありません。"))
            self._example_folder.setText("")
            self._example_name.setText("")
            return
        full = os.path.join(app_config.download_dir, example["relative_path"])
        self._example_title.setText(tr(f"For example: {example['title']}", f"示例（随机）：{example['title']}", f"例: {example['title']}"))
        self._example_folder.setText(tr(f"Saved in: {os.path.dirname(full)}", f"保存到：{os.path.dirname(full)}", f"保存先: {os.path.dirname(full)}"))
        self._example_name.setText(tr(f"Named: {os.path.basename(full)}", f"重命名为：{os.path.basename(full)}", f"ファイル名: {os.path.basename(full)}"))

    # ── actions ──────────────────────────────────────────────────────────────

    def _on_batch_changed(self, _index: int):
        if not self._syncing:
            self.show_requested.emit("", self._batch_id())
            self.refresh()

    def _on_rule_changed(self, index: int):
        rule_id = str(self._rule_combo.itemData(index) or "")
        if self._syncing or not rule_id:
            return
        download_manager.restage(rule_id, batch_id=self._batch_id())

    def _confirm(self):
        result = download_manager.confirm_staged(batch_id=self._batch_id())
        message = tr(
            f"Queued {result['queued']}, marked {result['marked']}; {result['dropped']} skipped rows cleared",
            f"已加入队列 {result['queued']} 个，标记 {result['marked']} 个；清除跳过/不能下的 {result['dropped']} 行",
            f"キュー {result['queued']} 件、記録 {result['marked']} 件。スキップ等 {result['dropped']} 行を削除",
        )
        InfoBar.success(
            title=tr("Confirmed", "已确认", "確認しました"), content=message,
            orient=Qt.Orientation.Horizontal, isClosable=True, position=InfoBarPosition.TOP, duration=3500, parent=self.window(),
        )

    def _clear(self):
        batch_id = self._batch_id()
        scope = self._batch_combo.currentText() if batch_id else tr("every batch", "全部批次", "すべてのバッチ")
        if show_fluent_confirmation(
            self.window(),
            tr("Clear waiting videos", "清空待确认", "確認待ちをクリア"),
            tr(f"Remove {scope} from the waiting list? Nothing is downloaded or deleted.",
               f"从待确认中移除「{scope}」？不会下载，也不会删除任何文件。",
               f"「{scope}」を確認待ちから外しますか？保存も削除もしません。"),
        ):
            download_manager.discard_staged(batch_id=batch_id)
