"""The workbench's confirmation bar for videos waiting to be downloaded.

Two lines above the task list, shown while anything waits.  First the
verdict and the decision: how many can download / are skipped / cannot
download (each filters the list), then Clear and Confirm download.  Then the
batch's settings: which batch, which rule (searchable; changing it re-checks
at once) and where a random video would be saved under which name.
"""

from __future__ import annotations

import os

from PySide6.QtCore import QStringListModel, Qt, QTimer, Signal
from PySide6.QtWidgets import QCompleter, QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    ComboBox,
    EditableComboBox,
    FluentIcon,
    InfoBar,
    InfoBarPosition,
    PrimaryPushButton,
    StrongBodyLabel,
    TransparentToolButton,
)

from ...config import app_config
from ...core.download.staging import STAGE_READY, STAGE_SKIP, STAGE_UNAVAILABLE
from ...core.rules import rule_store
from ...i18n import tr
from ...signal_bus import signal_bus
from ..fluent_dialogs import show_fluent_confirmation
from ..rules.rule_text import _rule_display_name
from ..theme import qcolor, set_secondary_text
from .page_dependencies import download_manager


class _CountChip(BodyLabel):
    """"11 can download" with a colored number; a click filters the task list."""

    clicked = Signal()

    def __init__(self, caption: str, role: str, tooltip: str, parent: QWidget):
        super().__init__(parent)
        self._caption, self._role = caption, role
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tooltip)

    def set_count(self, count: int):
        color = qcolor(self._role).name() if count else qcolor("text_secondary").name()
        self.setText(f'<b style="color:{color}">{count}</b>&nbsp;{self._caption}')

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()


class _ElidedLabel(BodyLabel):
    """One line, cut with "…" to the width it gets; the full text is in the tooltip."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self._full = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

    def set_full_text(self, text: str, tooltip: str = ""):
        self._full = text
        self.setToolTip(tooltip or text)
        self._elide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()

    def _elide(self):
        self.setText(self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideMiddle, max(20, self.width())))


class StagingPanel(CardWidget):
    """Confirm (or clear) the videos waiting on the workbench, one batch or all."""

    show_requested = Signal(str, str)  # verdict ("" = all), batch id ("" = all)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._syncing = False
        self._last_total = 0  # more waiting than last time: show them in the list
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 10, 12, 10)
        root.setSpacing(6)
        root.addLayout(self._build_decision_row())
        root.addLayout(self._build_settings_row())
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(80)
        self._refresh_timer.timeout.connect(self.refresh)
        signal_bus.staging_changed.connect(self._refresh_timer.start)
        signal_bus.rules_changed.connect(self._reload_rules)
        self._reload_rules()

    # ── building ─────────────────────────────────────────────────────────────

    def _build_decision_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(14)
        self._title = StrongBodyLabel("", self)
        row.addWidget(self._title)
        self._chips: dict[str, _CountChip] = {}
        for verdict, caption, role, tip in (
            (STAGE_READY, tr("can download", "可下载", "保存可"), "success",
             tr("Show the videos that will download", "只看将要下载的作品", "保存予定のみ表示")),
            (STAGE_SKIP, tr("skipped", "跳过", "スキップ"), "warning",
             tr("Already here, already queued, or stopped by the rule; right-click a row to allow it",
                "已有文件、已在队列或被规则拦住；可在行上右键放行", "保存済み・キュー内・ルール除外。行を右クリックで許可")),
            (STAGE_UNAVAILABLE, tr("cannot download", "不能下", "保存不可"), "danger",
             tr("The site does not offer the file (private, embedded…); the reason is on each row",
                "站点不提供文件（私有、外链等），每行有原因", "サイトがファイルを提供していません（理由は各行）")),
        ):
            chip = _CountChip(caption, role, tip, self)
            chip.clicked.connect(lambda verdict=verdict: self.show_requested.emit(verdict, self._batch_id()))
            self._chips[verdict] = chip
            row.addWidget(chip)
        row.addStretch(1)
        clear_btn = TransparentToolButton(FluentIcon.DELETE, self)
        clear_btn.setToolTip(tr("Clear the waiting videos (this batch)", "清空待确认（当前批次）", "確認待ちをクリア"))
        clear_btn.clicked.connect(self._clear)
        row.addWidget(clear_btn)
        self._confirm_btn = PrimaryPushButton("", self, FluentIcon.DOWNLOAD)
        self._confirm_btn.setMinimumWidth(150)
        self._confirm_btn.clicked.connect(self._confirm)
        row.addWidget(self._confirm_btn)
        return row

    def _build_settings_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        self._batch_combo = ComboBox(self)
        self._batch_combo.setFixedWidth(190)
        self._batch_combo.setToolTip(tr("Which batch", "批次", "バッチ"))
        self._batch_combo.currentIndexChanged.connect(self._on_batch_changed)
        row.addWidget(self._batch_combo)
        self._rule_combo = EditableComboBox(self)
        self._rule_combo.setFixedWidth(200)
        self._rule_combo.setPlaceholderText(tr("Download rule…", "下载规则…", "ルール…"))
        self._rule_combo.setToolTip(tr("Download rule; changing it re-checks the batch", "下载规则；更换后立即重新预解析", "ルール。変更すると再確認します"))
        self._rule_combo.currentIndexChanged.connect(self._on_rule_changed)
        row.addWidget(self._rule_combo)
        self._example = _ElidedLabel(self)
        set_secondary_text(self._example)
        row.addWidget(self._example, 1)
        reroll = TransparentToolButton(FluentIcon.SYNC, self)
        reroll.setToolTip(tr("Another random example", "换一个示例", "別の例"))
        reroll.clicked.connect(self._show_example)
        row.addWidget(reroll)
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
        self._title.setText(tr(f"To confirm {total}", f"待确认 {total}", f"確認待ち {total}"))
        current = self._batch_id()
        counts = download_manager.staged_counts(current)
        for verdict, chip in self._chips.items():
            chip.set_count(counts[verdict])
        self._select_rule(next((b["rule_id"] for b in batches if not current or b["batch_id"] == current), ""))
        ready = counts[STAGE_READY]
        self._confirm_btn.setText(tr(f"Download ({ready})", f"确认下载 ({ready})", f"保存する ({ready})"))
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
            self._example.set_full_text(tr("Nothing here would download yet.", "这里还没有将要下载的作品。", "保存予定の動画はまだありません。"))
            return
        full = os.path.join(app_config.download_dir, example["relative_path"])
        title = example["title"]
        self._example.set_full_text(
            tr(f"e.g. {title} → {full}", f"示例：{title} → {full}", f"例: {title} → {full}"),
            tr(f"Random example\n{title}\nSaved in: {os.path.dirname(full)}\nNamed: {os.path.basename(full)}",
               f"随机示例\n{title}\n保存到：{os.path.dirname(full)}\n重命名为：{os.path.basename(full)}",
               f"ランダムな例\n{title}\n保存先: {os.path.dirname(full)}\nファイル名: {os.path.basename(full)}"),
        )

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
