"""Named download/filter rules page and reusable rule picker."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    ComboBox,
    FluentIcon,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    SubtitleLabel,
    SwitchButton,
    TitleLabel,
)

from ..core.rules import (
    apply_rule_payload,
    current_rule_payload,
    default_rule_payload,
    normalize_rule_payload,
    rule_store,
)
from ..i18n import tr
from ..signal_bus import signal_bus


class RuleFormWidget(QWidget):
    """Editor for one named rule, shared by the rules page and picker flows."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._build_ui()
        self.load_payload(default_rule_payload())

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        identity = CardWidget(self)
        identity_layout = QFormLayout(identity)
        identity_layout.setContentsMargins(18, 14, 18, 14)
        identity_layout.setSpacing(10)
        self.name_edit = LineEdit(identity)
        self.name_edit.setPlaceholderText(tr("e.g. MMD favorites", "例如：MMD 收藏", "例: MMD お気に入り"))
        identity_layout.addRow(BodyLabel(tr("Rule name", "规则名称", "ルール名"), identity), self.name_edit)
        root.addWidget(identity)

        filter_card = CardWidget(self)
        filter_layout = QGridLayout(filter_card)
        filter_layout.setContentsMargins(18, 14, 18, 14)
        filter_layout.setHorizontalSpacing(10)
        filter_layout.setVerticalSpacing(10)
        filter_layout.addWidget(SubtitleLabel(tr("Metadata filters", "元数据筛选", "メタデータフィルター"), filter_card), 0, 0, 1, 4)

        self.filter_enabled = SwitchButton(filter_card)
        filter_layout.addWidget(BodyLabel(tr("Enable filters", "启用筛选", "フィルターを启用"), filter_card), 1, 0)
        filter_layout.addWidget(self.filter_enabled, 1, 1)
        self.likes_enabled = SwitchButton(filter_card)
        self.likes_edit = LineEdit(filter_card)
        self.likes_edit.setPlaceholderText("0")
        filter_layout.addWidget(BodyLabel(tr("Likes >=", "点赞数 >=", "いいね数 >="), filter_card), 2, 0)
        filter_layout.addWidget(self.likes_enabled, 2, 1)
        filter_layout.addWidget(self.likes_edit, 2, 2, 1, 2)
        self.views_enabled = SwitchButton(filter_card)
        self.views_edit = LineEdit(filter_card)
        self.views_edit.setPlaceholderText("0")
        filter_layout.addWidget(BodyLabel(tr("Views >=", "播放数 >=", "再生数 >="), filter_card), 3, 0)
        filter_layout.addWidget(self.views_enabled, 3, 1)
        filter_layout.addWidget(self.views_edit, 3, 2, 1, 2)
        self.date_enabled = SwitchButton(filter_card)
        self.start_edit = LineEdit(filter_card)
        self.start_edit.setPlaceholderText("1970-01-01")
        self.end_edit = LineEdit(filter_card)
        self.end_edit.setPlaceholderText(datetime.now().strftime("%Y-%m-%d"))
        filter_layout.addWidget(BodyLabel(tr("Date range", "日期范围", "日付範囲"), filter_card), 4, 0)
        filter_layout.addWidget(self.date_enabled, 4, 1)
        filter_layout.addWidget(self.start_edit, 4, 2)
        filter_layout.addWidget(self.end_edit, 4, 3)
        self.include_tags_enabled = SwitchButton(filter_card)
        self.include_tags_edit = LineEdit(filter_card)
        self.include_tags_edit.setPlaceholderText(tr("2d,mmd", "2d,mmd", "2d,mmd"))
        filter_layout.addWidget(BodyLabel(tr("Include tags", "包含标签", "含めるタグ"), filter_card), 5, 0)
        filter_layout.addWidget(self.include_tags_enabled, 5, 1)
        filter_layout.addWidget(self.include_tags_edit, 5, 2, 1, 2)
        self.exclude_tags_enabled = SwitchButton(filter_card)
        self.exclude_tags_edit = LineEdit(filter_card)
        self.exclude_tags_edit.setPlaceholderText(tr("vr,ai", "vr,ai", "vr,ai"))
        filter_layout.addWidget(BodyLabel(tr("Exclude tags", "排除标签", "除外タグ"), filter_card), 6, 0)
        filter_layout.addWidget(self.exclude_tags_enabled, 6, 1)
        filter_layout.addWidget(self.exclude_tags_edit, 6, 2, 1, 2)
        root.addWidget(filter_card)

        title_card = CardWidget(self)
        title_layout = QFormLayout(title_card)
        title_layout.setContentsMargins(18, 14, 18, 14)
        title_layout.setSpacing(10)
        self.title_include_edit = LineEdit(title_card)
        self.title_include_edit.setPlaceholderText(tr("Any keyword, comma separated", "任一关键词，逗号分隔", "キーワード（カンマ区切り）"))
        self.title_exclude_edit = LineEdit(title_card)
        self.title_exclude_edit.setPlaceholderText(tr("Any keyword, comma separated", "任一关键词，逗号分隔", "キーワード（カンマ区切り）"))
        title_layout.addRow(SubtitleLabel(tr("Title filters", "标题筛选", "タイトルフィルター"), title_card))
        title_layout.addRow(BodyLabel(tr("Include", "包含", "含む"), title_card), self.title_include_edit)
        title_layout.addRow(BodyLabel(tr("Exclude", "排除", "除外"), title_card), self.title_exclude_edit)
        root.addWidget(title_card)

        download_card = CardWidget(self)
        download_layout = QGridLayout(download_card)
        download_layout.setContentsMargins(18, 14, 18, 14)
        download_layout.setHorizontalSpacing(10)
        download_layout.setVerticalSpacing(10)
        download_layout.addWidget(SubtitleLabel(tr("Download behavior", "下载行为", "保存動作"), download_card), 0, 0, 1, 2)
        self.download_video = SwitchButton(download_card)
        self.mark_only = SwitchButton(download_card)
        self.download_thumb = SwitchButton(download_card)
        self.collect_nfo = SwitchButton(download_card)
        download_layout.addWidget(BodyLabel(tr("Download video", "下载视频", "動画を保存"), download_card), 1, 0)
        download_layout.addWidget(self.download_video, 1, 1)
        download_layout.addWidget(BodyLabel(tr("Mark only", "仅标记已下载", "マークのみ"), download_card), 2, 0)
        download_layout.addWidget(self.mark_only, 2, 1)
        download_layout.addWidget(BodyLabel(tr("Download thumbnail", "下载封面", "サムネイルを保存"), download_card), 3, 0)
        download_layout.addWidget(self.download_thumb, 3, 1)
        download_layout.addWidget(BodyLabel("NFO", download_card), 4, 0)
        download_layout.addWidget(self.collect_nfo, 4, 1)
        root.addWidget(download_card)
        root.addStretch(1)

    def load_payload(self, payload: dict[str, Any]):
        data = normalize_rule_payload(payload)
        self.filter_enabled.setChecked(data["filter_enabled"])
        self.likes_enabled.setChecked(data["filter_min_likes_enabled"])
        self.likes_edit.setText(str(data["filter_min_likes"]))
        self.views_enabled.setChecked(data["filter_min_views_enabled"])
        self.views_edit.setText(str(data["filter_min_views"]))
        self.date_enabled.setChecked(data["filter_date_enabled"])
        self.start_edit.setText(data["filter_start_date"])
        self.end_edit.setText(data["filter_end_date"])
        self.include_tags_enabled.setChecked(data["filter_include_tags_enabled"])
        self.include_tags_edit.setText(data["filter_include_tags"])
        self.exclude_tags_enabled.setChecked(data["filter_exclude_tags_enabled"])
        self.exclude_tags_edit.setText(data["filter_exclude_tags"])
        self.title_include_edit.setText(data["title_include"])
        self.title_exclude_edit.setText(data["title_exclude"])
        self.download_video.setChecked(data["download_video_file"])
        self.mark_only.setChecked(data["mark_submitted_as_downloaded"])
        self.download_thumb.setChecked(data["download_thumbnail"])
        self.collect_nfo.setChecked(data["collect_nfo_info"])

    def payload(self) -> dict[str, Any]:
        try:
            likes = max(0, int((self.likes_edit.text() or "0").strip()))
            views = max(0, int((self.views_edit.text() or "0").strip()))
            start = (self.start_edit.text() or "1970-01-01").strip()
            end = (self.end_edit.text() or "").strip()
            datetime.strptime(start, "%Y-%m-%d")
            if end:
                datetime.strptime(end, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        return normalize_rule_payload(
            {
                "filter_enabled": self.filter_enabled.isChecked(),
                "filter_min_likes_enabled": self.likes_enabled.isChecked(),
                "filter_min_likes": likes,
                "filter_min_views_enabled": self.views_enabled.isChecked(),
                "filter_min_views": views,
                "filter_date_enabled": self.date_enabled.isChecked(),
                "filter_start_date": start,
                "filter_end_date": end,
                "filter_include_tags_enabled": self.include_tags_enabled.isChecked(),
                "filter_include_tags": self.include_tags_edit.text(),
                "filter_exclude_tags_enabled": self.exclude_tags_enabled.isChecked(),
                "filter_exclude_tags": self.exclude_tags_edit.text(),
                "title_include": self.title_include_edit.text(),
                "title_exclude": self.title_exclude_edit.text(),
                "download_video_file": self.download_video.isChecked(),
                "download_thumbnail": self.download_thumb.isChecked(),
                "collect_nfo_info": self.collect_nfo.isChecked(),
                "mark_submitted_as_downloaded": self.mark_only.isChecked(),
            }
        )


class RulePicker(QWidget):
    """Searchable rule selector used beside download actions."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.search_edit = LineEdit(self)
        self.search_edit.setPlaceholderText(tr("Search saved rules…", "搜索已保存规则…", "保存済みルールを検索…"))
        self.search_edit.setClearButtonEnabled(True)
        self.combo = ComboBox(self)
        self.combo.setMinimumWidth(180)
        self.apply_btn = PrimaryPushButton(tr("Apply", "应用规则", "適用"), self, FluentIcon.ACCEPT)
        self.apply_btn.setMinimumWidth(96)
        self.apply_btn.clicked.connect(self.apply_selected)
        self._rules_cache: list[dict[str, Any]] = []
        self.search_edit.textChanged.connect(self._filter_items)
        signal_bus.rules_changed.connect(self.refresh_rules)
        self.refresh_rules()
        layout.addWidget(self.search_edit, 1)
        layout.addWidget(self.combo, 1)
        layout.addWidget(self.apply_btn)

    def refresh_rules(self):
        selected = self.selected_rule_id()
        self._rules_cache = rule_store.list_rules()
        self._rebuild_combo(selected)

    def _rebuild_combo(self, selected: str = ""):
        query = self.search_edit.text().strip().casefold()
        self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.addItem(tr("No rule", "不使用规则", "ルールなし"), userData="")
        for rule in self._rules_cache:
            if query and query not in str(rule.get("name", "")).casefold():
                continue
            self.combo.addItem(str(rule["name"]), userData=str(rule["id"]))
        self.combo.blockSignals(False)
        if selected:
            self.select_rule(selected)

    def _filter_items(self, _text: str):
        self._rebuild_combo(self.selected_rule_id())

    def selected_rule_id(self) -> str:
        return str(self.combo.currentData() or "")

    def selected_rule(self) -> dict[str, Any] | None:
        rule_id = self.selected_rule_id()
        return next((r for r in self._rules_cache if str(r.get("id")) == rule_id), None)

    def select_rule(self, rule_id: str):
        for index in range(self.combo.count()):
            if str(self.combo.itemData(index) or "") == str(rule_id):
                self.combo.setCurrentIndex(index)
                return

    def apply_selected(self) -> bool:
        rule = self.selected_rule()
        if not rule:
            return False
        apply_rule_payload(rule["payload"])
        signal_bus.download_options_changed.emit()
        InfoBar.success(
            title=tr("Rule applied", "规则已应用", "ルールを適用しました"),
            content=str(rule["name"]),
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=1600,
            parent=self.window(),
        )
        return True


class RulesInterface(QWidget):
    """Manage named filter/download presets."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("RulesInterface")
        self._selected_id = ""
        self._build_ui()
        self._reload_list()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 22, 28, 18)
        root.setSpacing(12)
        root.addWidget(TitleLabel(tr("Download Rules", "下载规则", "ダウンロードルール"), self))
        root.addWidget(BodyLabel(tr("Save reusable filters and download behavior, then apply them before submitting or downloading.", "把筛选条件和下载行为保存成可复用规则，在提交或下载前直接应用。", "フィルターと保存動作をルールとして保存し、送信前に適用できます。"), self))

        splitter = QHBoxLayout()
        root.addLayout(splitter, 1)
        left = CardWidget(self)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(12, 12, 12, 12)
        left_layout.addWidget(SubtitleLabel(tr("Saved rules", "已保存规则", "保存済みルール"), left))
        self._list = QListWidget(left)
        self._list.currentItemChanged.connect(self._on_rule_selected)
        left_layout.addWidget(self._list, 1)
        action_row = QHBoxLayout()
        new_btn = PrimaryPushButton(tr("New", "新建", "新規"), left, FluentIcon.ADD)
        duplicate_btn = PushButton(tr("Duplicate", "复制", "複製"), left)
        delete_btn = PushButton(tr("Delete", "删除", "削除"), left)
        new_btn.clicked.connect(self._new_rule)
        duplicate_btn.clicked.connect(self._duplicate_rule)
        delete_btn.clicked.connect(self._delete_rule)
        action_row.addWidget(new_btn)
        action_row.addWidget(duplicate_btn)
        action_row.addWidget(delete_btn)
        left_layout.addLayout(action_row)
        splitter.addWidget(left, 1)

        right = QWidget(self)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self._form_scroll = QScrollArea(right)
        self._form_scroll.setWidgetResizable(True)
        self._form_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._form = RuleFormWidget()
        self._form_scroll.setWidget(self._form)
        right_layout.addWidget(self._form_scroll, 1)
        bottom = QHBoxLayout()
        self._apply_btn = PrimaryPushButton(tr("Apply to current settings", "应用到当前设置", "現在の設定に適用"), right, FluentIcon.ACCEPT)
        save_btn = PrimaryPushButton(tr("Save rule", "保存规则", "ルールを保存"), right, FluentIcon.SAVE)
        self._apply_btn.clicked.connect(self._apply_current)
        save_btn.clicked.connect(self._save_rule)
        bottom.addStretch()
        bottom.addWidget(self._apply_btn)
        bottom.addWidget(save_btn)
        right_layout.addLayout(bottom)
        splitter.addWidget(right, 2)

    def _reload_list(self, select_id: str | None = None):
        rules = rule_store.list_rules()
        self._list.blockSignals(True)
        self._list.clear()
        for rule in rules:
            item = QListWidgetItem(rule["name"])
            item.setData(Qt.ItemDataRole.UserRole, rule["id"])
            self._list.addItem(item)
        self._list.blockSignals(False)
        if select_id:
            for index in range(self._list.count()):
                if self._list.item(index).data(Qt.ItemDataRole.UserRole) == select_id:
                    self._list.setCurrentRow(index)
                    return
        if self._list.count():
            self._list.setCurrentRow(0)
        else:
            self._selected_id = ""
            self._form.name_edit.setText("")
            self._form.load_payload(current_rule_payload())

    def _on_rule_selected(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None):
        if current is None:
            return
        rule_id = str(current.data(Qt.ItemDataRole.UserRole) or "")
        rule = next((item for item in rule_store.list_rules() if item["id"] == rule_id), None)
        if rule:
            self._selected_id = rule_id
            self._form.name_edit.setText(rule["name"])
            self._form.load_payload(rule["payload"])

    def _new_rule(self):
        self._selected_id = ""
        self._list.clearSelection()
        self._form.name_edit.setText("")
        self._form.load_payload(current_rule_payload())
        self._form.name_edit.setFocus()

    def _duplicate_rule(self):
        try:
            payload = self._form.payload()
        except ValueError as exc:
            InfoBar.error(title=tr("Invalid rule", "规则无效", "ルールが無効"), content=str(exc), orient=Qt.Orientation.Horizontal, isClosable=True, position=InfoBarPosition.TOP, duration=2200, parent=self)
            return
        self._selected_id = ""
        self._list.clearSelection()
        self._form.name_edit.setText((self._form.name_edit.text() or "未命名规则") + " - 副本")
        self._form.load_payload(payload)

    def _save_rule(self):
        try:
            payload = self._form.payload()
            rule = rule_store.save(self._form.name_edit.text(), payload, self._selected_id or None)
        except ValueError as exc:
            InfoBar.error(title=tr("Cannot save rule", "无法保存规则", "ルールを保存できません"), content=str(exc), orient=Qt.Orientation.Horizontal, isClosable=True, position=InfoBarPosition.TOP, duration=2500, parent=self)
            return
        self._selected_id = rule["id"]
        self._reload_list(self._selected_id)
        signal_bus.rules_changed.emit()
        InfoBar.success(title=tr("Rule saved", "规则已保存", "ルールを保存しました"), content=rule["name"], orient=Qt.Orientation.Horizontal, isClosable=True, position=InfoBarPosition.TOP, duration=1800, parent=self)

    def _delete_rule(self):
        if not self._selected_id:
            return
        answer = QMessageBox.question(self, tr("Delete rule", "删除规则", "ルールを削除"), tr("Delete the selected rule?", "确定删除当前规则？", "選択したルールを削除しますか？"))
        if answer != QMessageBox.StandardButton.Yes:
            return
        if rule_store.delete(self._selected_id):
            self._selected_id = ""
            self._reload_list()
            signal_bus.rules_changed.emit()

    def _apply_current(self):
        try:
            payload = self._form.payload()
        except ValueError as exc:
            InfoBar.error(title=tr("Invalid rule", "规则参数无效", "ルール値が不正"), content=str(exc), orient=Qt.Orientation.Horizontal, isClosable=True, position=InfoBarPosition.TOP, duration=2500, parent=self)
            return
        apply_rule_payload(payload)
        signal_bus.download_options_changed.emit()
        InfoBar.success(title=tr("Rule applied", "规则已应用", "ルールを適用しました"), content=self._form.name_edit.text(), orient=Qt.Orientation.Horizontal, isClosable=True, position=InfoBarPosition.TOP, duration=1800, parent=self)

    def refresh_theme_styles(self):
        self._reload_list(self._selected_id or None)
