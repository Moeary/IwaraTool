"""Named download/filter rules page and reusable rule picker."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from PySide6.QtCore import QPoint, QSize, QStringListModel, QTimer, Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QCompleter,
    QAbstractItemView,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    EditableComboBox,
    FluentIcon,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    SubtitleLabel,
    SwitchButton,
    TitleLabel,
    isDarkTheme,
)

from ..core.rules import (
    BUILTIN_DEFAULT_RULE_ID,
    active_rule_id,
    apply_rule_payload,
    current_rule_payload,
    default_rule_payload,
    normalize_rule_payload,
    rule_store,
    set_active_rule_id,
)
from ..core.download_paths import validate_filename_template
from ..core.manager import download_manager
from ..core.tag_dictionary import TagSuggestion
from ..i18n import tr
from ..signal_bus import signal_bus
from .ui_state import show_fluent_confirmation


DRAFT_RULE_ID = "__draft_rule__"


def _style_rule_splitter(splitter: QSplitter):
    """Keep the rule editor splitter visible without a native white strip."""

    if isDarkTheme():
        handle = "rgba(255, 255, 255, 0.08)"
        hover = "rgba(255, 255, 255, 0.18)"
        pressed = "rgba(255, 255, 255, 0.28)"
    else:
        handle = "rgba(0, 0, 0, 0.04)"
        hover = "rgba(0, 0, 0, 0.10)"
        pressed = "rgba(0, 0, 0, 0.18)"
    splitter.setStyleSheet(
        f"""
        QSplitter::handle {{ background: {handle}; }}
        QSplitter::handle:horizontal {{ width: 10px; }}
        QSplitter::handle:vertical {{ height: 10px; }}
        QSplitter::handle:hover {{ background: {hover}; }}
        QSplitter::handle:pressed {{ background: {pressed}; }}
        """
    )


def _rule_summary(payload: dict[str, Any]) -> str:
    data = normalize_rule_payload(payload)
    actions = [tr("mark only", "仅标记", "マークのみ") if data["mark_submitted_as_downloaded"] else tr("video", "视频", "動画")]
    if data["download_thumbnail"]:
        actions.append(tr("cover", "封面", "サムネイル"))
    if data["collect_nfo_info"]:
        actions.append("NFO")
    if not data["record_to_history"]:
        actions.append(tr("no history", "不写历史", "履歴なし"))
    filters: list[str] = []
    if data["filter_enabled"]:
        if data["filter_min_likes_enabled"]:
            filters.append(f"{tr('likes', '点赞', 'いいね')}≥{data['filter_min_likes']}")
        if data["filter_min_views_enabled"]:
            filters.append(f"{tr('views', '播放', '再生')}≥{data['filter_min_views']}")
        if data["filter_date_enabled"]:
            filters.append(tr("date", "日期", "日付"))
        if data["filter_include_tags_enabled"] or data["filter_exclude_tags_enabled"]:
            filters.append(tr("tags", "标签", "タグ"))
    if data["title_include"] or data["title_exclude"]:
        filters.append(tr("title keywords", "标题关键词", "タイトル語句"))
    return f"{' + '.join(actions)} · {' · '.join(filters) if filters else tr('no filters', '无筛选', 'フィルターなし')}"


def _rule_display_name(rule: dict[str, Any]) -> str:
    if rule.get("builtin"):
        return tr("Default Download", "默认下载", "既定ダウンロード")
    return str(rule.get("name", "") or "")


class _RuleTagSuggestionPopup(QListWidget):
    """Localized tag candidates used by the include/exclude rule fields."""

    suggestion_chosen = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setMinimumWidth(360)
        self.setMaximumHeight(260)
        self.setStyleSheet(
            f"""
            QListWidget {{
                background: {"#252a31" if isDarkTheme() else "#ffffff"};
                border: 1px solid {"#4a5563" if isDarkTheme() else "#d7dce2"};
                border-radius: 8px;
                padding: 4px;
            }}
            QListWidget::item {{ padding: 7px 9px; border-radius: 5px; }}
            QListWidget::item:selected {{
                background: {"#304b5b" if isDarkTheme() else "#dff4fa"};
                color: {"#ffffff" if isDarkTheme() else "#12313a"};
            }}
            """
        )
        self.itemClicked.connect(self._choose_item)

    def set_suggestions(self, suggestions: list[TagSuggestion]):
        self.clear()
        for suggestion in suggestions:
            item = QListWidgetItem(suggestion.display_text)
            item.setData(Qt.ItemDataRole.UserRole, suggestion.key)
            item.setToolTip(
                f"{suggestion.key}\n"
                f"EN: {suggestion.en}\n"
                f"中文: {suggestion.zh}\n"
                f"日本語: {suggestion.ja}"
            )
            self.addItem(item)
        if self.count():
            self.setCurrentRow(0)

    def _choose_item(self, item: QListWidgetItem):
        value = item.data(Qt.ItemDataRole.UserRole)
        if value:
            self.suggestion_chosen.emit(str(value))


class RuleFormWidget(QWidget):
    """Editor for one named rule, shared by the rules page and picker flows."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._tag_popup = _RuleTagSuggestionPopup(self)
        self._tag_popup.suggestion_chosen.connect(self._apply_tag_suggestion)
        self._active_tag_edit: LineEdit | None = None
        self._loading_payload = False
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
        self.summary_label = BodyLabel("", identity)
        self.summary_label.setWordWrap(True)
        identity_layout.addRow(BodyLabel(tr("Summary", "规则摘要", "概要"), identity), self.summary_label)
        root.addWidget(identity)

        storage_card = CardWidget(self)
        storage_layout = QGridLayout(storage_card)
        storage_layout.setContentsMargins(18, 14, 18, 14)
        storage_layout.setHorizontalSpacing(10)
        storage_layout.setVerticalSpacing(10)
        storage_layout.addWidget(SubtitleLabel(tr("Filename template", "下载命名规则", "ファイル名テンプレート"), storage_card), 0, 0, 1, 4)
        self.filename_template_edit = LineEdit(storage_card)
        self.filename_template_edit.setPlaceholderText("{username}/{YYYY-MM-DD}_{title}_{id}.mp4")
        storage_layout.addWidget(self.filename_template_edit, 1, 0, 1, 3)
        self.validate_template_btn = PushButton(
            tr("Validate", "检验规则", "検証"),
            storage_card,
            FluentIcon.ACCEPT,
        )
        self.validate_template_btn.setMinimumWidth(112)
        self.validate_template_btn.setToolTip(
            tr(
                "Check whether the naming template is valid",
                "检查命名规则是否有效",
                "命名テンプレートの有効性を確認",
            )
        )
        self.validate_template_btn.clicked.connect(self._validate_filename_template)
        storage_layout.addWidget(self.validate_template_btn, 1, 3)
        template_help = BodyLabel(
            tr(
                "Available: {username} {author} {YYYY-MM-DD} {YYYY} {MM} {DD} {title} {id} {quality} {views} {likes}",
                "可用占位符：{username} {author} {YYYY-MM-DD} {YYYY} {MM} {DD} {title} {id} {quality} {views} {likes}",
                "使用可能: {username} {author} {YYYY-MM-DD} {YYYY} {MM} {DD} {title} {id} {quality} {views} {likes}",
            ),
            storage_card,
        )
        template_help.setWordWrap(True)
        storage_layout.addWidget(template_help, 2, 0, 1, 4)
        root.addWidget(storage_card)

        filter_card = CardWidget(self)
        filter_layout = QGridLayout(filter_card)
        filter_layout.setContentsMargins(18, 14, 18, 14)
        filter_layout.setHorizontalSpacing(10)
        filter_layout.setVerticalSpacing(10)
        filter_layout.addWidget(SubtitleLabel(tr("Metadata filters", "元数据筛选", "メタデータフィルター"), filter_card), 0, 0, 1, 4)

        self.filter_enabled = SwitchButton(filter_card)
        filter_layout.addWidget(BodyLabel(tr("Enable filters", "启用筛选", "フィルターを有効化"), filter_card), 1, 0)
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
        for edit in (self.include_tags_edit, self.exclude_tags_edit):
            edit.textChanged.connect(
                lambda text, target=edit: self._show_tag_suggestions(target, text)
            )
            edit.editingFinished.connect(self._hide_tag_suggestions)
        filter_hint = BodyLabel(
            tr(
                "Type a Chinese or English tag and choose a candidate; the canonical Iwara tag will be inserted.",
                "输入中文或英文标签并选择候选项，系统会自动填入 Iwara 标准标签。",
                "中国語または英語のタグを入力して候補を選ぶと、Iwara標準タグを自動入力します。",
            ),
            filter_card,
        )
        filter_hint.setWordWrap(True)
        filter_layout.addWidget(filter_hint, 7, 0, 1, 4)
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
        download_layout.addWidget(SubtitleLabel(tr("Download behavior", "下载行为", "保存動作"), download_card), 0, 0, 1, 4)
        self.download_video = SwitchButton(download_card)
        self.mark_only = SwitchButton(download_card)
        self.download_thumb = SwitchButton(download_card)
        self.collect_nfo = SwitchButton(download_card)
        self.record_history = SwitchButton(download_card)
        download_layout.addWidget(BodyLabel(tr("Download video", "下载视频", "動画を保存"), download_card), 1, 0)
        download_layout.addWidget(self.download_video, 1, 1)
        download_layout.addWidget(BodyLabel(tr("Mark only", "仅标记已下载", "マークのみ"), download_card), 1, 2)
        download_layout.addWidget(self.mark_only, 1, 3)
        download_layout.addWidget(BodyLabel(tr("Download thumbnail", "下载封面", "サムネイルを保存"), download_card), 2, 0)
        download_layout.addWidget(self.download_thumb, 2, 1)
        download_layout.addWidget(BodyLabel("NFO", download_card), 2, 2)
        download_layout.addWidget(self.collect_nfo, 2, 3)
        download_layout.addWidget(BodyLabel(tr("Write to history", "记录到历史", "履歴へ記録"), download_card), 3, 0)
        download_layout.addWidget(self.record_history, 3, 1)
        self.record_history_hint = BodyLabel("", download_card)
        self.record_history_hint.setWordWrap(True)
        download_layout.addWidget(
            self.record_history_hint,
            3,
            2,
            1,
            2,
        )
        self.record_history.checkedChanged.connect(self._update_record_history_hint)
        self._update_record_history_hint()
        root.addWidget(download_card)
        root.addStretch(1)

    def _show_tag_suggestions(self, edit: LineEdit, text: str):
        if self._loading_payload:
            return
        query = str(text or "").rsplit(",", 1)[-1].strip()
        if not query or not self.isVisible():
            self._hide_tag_suggestions()
            return
        suggestions = download_manager.get_search_tag_suggestions(query, limit=12)
        if not suggestions:
            self._hide_tag_suggestions()
            return
        self._active_tag_edit = edit
        self._tag_popup.set_suggestions(suggestions)
        self._tag_popup.resize(
            min(560, max(360, edit.width())),
            min(260, max(60, self._tag_popup.sizeHint().height())),
        )
        self._tag_popup.move(edit.mapToGlobal(QPoint(0, edit.height())))
        self._tag_popup.show()
        self._tag_popup.raise_()

    def _hide_tag_suggestions(self):
        self._tag_popup.hide()

    def _apply_tag_suggestion(self, key: str):
        edit = self._active_tag_edit
        if edit is None:
            return
        self._hide_tag_suggestions()
        text = edit.text()
        comma = text.rfind(",")
        prefix = text[: comma + 1].rstrip() if comma >= 0 else ""
        edit.setText(f"{prefix} {key},".strip() + " ")
        edit.setFocus(Qt.FocusReason.OtherFocusReason)
        edit.setCursorPosition(len(edit.text()))
        QTimer.singleShot(0, lambda target=edit: self._restore_tag_edit_focus(target))

    def _restore_tag_edit_focus(self, edit: LineEdit):
        if edit is not self._active_tag_edit or not edit.isVisible() or not edit.isEnabled():
            return
        edit.setFocus(Qt.FocusReason.OtherFocusReason)
        edit.setCursorPosition(len(edit.text()))

    def _update_record_history_hint(self, checked: bool | None = None):
        enabled = self.record_history.isChecked() if checked is None else bool(checked)
        self.record_history_hint.setText(
            tr(
                "Downloaded content will be saved to history.",
                "下载内容会保存到历史。",
                "ダウンロード内容を履歴に保存します。",
            )
            if enabled
            else tr(
                "Downloaded content will no longer be saved to history.",
                "不再将下载内容保存到历史。",
                "ダウンロード内容を履歴に保存しません。",
            )
        )

    def _validate_filename_template(self):
        valid, reason = validate_filename_template(self.filename_template_edit.text())
        if valid:
            InfoBar.success(
                title=tr("Valid template", "命名规则有效", "有効なテンプレート"),
                content=tr(
                    "The template can be used safely.",
                    "该命名规则可以安全使用。",
                    "この命名テンプレートは安全に使用できます。",
                ),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=2200,
                parent=self.window() or self,
            )
            return True
        InfoBar.error(
            title=tr("Invalid template", "命名规则无效", "無効なテンプレート"),
            content=reason,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=3000,
            parent=self.window() or self,
        )
        return False

    def set_builtin_mode(self, builtin: bool):
        self.name_edit.setReadOnly(builtin)

    def load_payload(self, payload: dict[str, Any]):
        self._hide_tag_suggestions()
        self._loading_payload = True
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
        self.record_history.setChecked(data["record_to_history"])
        self._update_record_history_hint()
        self.filename_template_edit.setText(data["filename_template"])
        self.summary_label.setText(_rule_summary(data))
        self._loading_payload = False

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
        valid, reason = validate_filename_template(self.filename_template_edit.text())
        if not valid:
            raise ValueError(f"命名规则无效：{reason}")
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
                "record_to_history": self.record_history.isChecked(),
                "filename_template": self.filename_template_edit.text(),
            }
        )


class RulePicker(QWidget):
    """Single-line Fluent searchable selector that applies a rule immediately."""

    ruleApplied = Signal(dict)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._rules_cache: list[dict[str, Any]] = []
        self._syncing = False
        self._model = QStringListModel(self)
        self.combo = EditableComboBox(self)
        self.combo.setPlaceholderText(tr("Search or select a rule…", "搜索或选择下载规则…", "ルールを検索または選択…"))
        self.combo.setMinimumWidth(240)
        self.combo.setToolTip(tr("Selecting applies it immediately", "选中后立即应用并设为默认", "選択するとすぐ適用されます"))
        completer = QCompleter(self._model, self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        completer.setMaxVisibleItems(8)
        self.combo.setCompleter(completer)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.combo, 1)
        self.combo.currentIndexChanged.connect(self._on_combo_changed)
        signal_bus.rules_changed.connect(self.refresh_rules)
        signal_bus.active_rule_changed.connect(self._sync_active_rule)
        self.refresh_rules(apply_current=True)

    def refresh_rules(self, apply_current: bool = False):
        selected = active_rule_id()
        self._rules_cache = rule_store.list_available()
        if not any(rule["id"] == selected for rule in self._rules_cache):
            selected = BUILTIN_DEFAULT_RULE_ID
            set_active_rule_id(selected)
        self._rebuild_combo(selected)
        if apply_current:
            self._activate_rule(selected, show_notice=False, broadcast=False)

    def _rebuild_combo(self, selected: str):
        self._syncing = True
        self.combo.blockSignals(True)
        self.combo.clear()
        labels: list[str] = []
        for rule in self._rules_cache:
            suffix = tr(" · built in", " · 内置", " · 内蔵") if rule.get("builtin") else ""
            label = _rule_display_name(rule) + suffix
            labels.append(label)
            self.combo.addItem(label, userData=rule["id"])
        self._model.setStringList(labels)
        self.select_rule(selected)
        self.combo.blockSignals(False)
        self._syncing = False

    def selected_rule_id(self) -> str:
        return str(self.combo.currentData() or BUILTIN_DEFAULT_RULE_ID)

    def selected_rule(self) -> dict[str, Any] | None:
        rule_id = self.selected_rule_id()
        return next((r for r in self._rules_cache if r["id"] == rule_id), None)

    def select_rule(self, rule_id: str):
        for index in range(self.combo.count()):
            if str(self.combo.itemData(index) or "") == str(rule_id):
                self.combo.setCurrentIndex(index)
                return

    def _on_combo_changed(self, _index: int):
        if not self._syncing:
            self._activate_rule(self.selected_rule_id(), show_notice=True, broadcast=True)

    def _sync_active_rule(self, rule_id: str):
        if rule_id == self.selected_rule_id():
            return
        self._rebuild_combo(rule_id)
        rule = self.selected_rule()
        if rule:
            self.ruleApplied.emit(normalize_rule_payload(rule["payload"]))

    def _activate_rule(self, rule_id: str, *, show_notice: bool, broadcast: bool) -> bool:
        rule = next((r for r in self._rules_cache if r["id"] == rule_id), None)
        if rule is None:
            rule = self._rules_cache[0] if self._rules_cache else None
        if rule is None:
            return False
        normalized = apply_rule_payload(rule["payload"])
        set_active_rule_id(rule["id"])
        signal_bus.download_options_changed.emit()
        self.ruleApplied.emit(normalized)
        if broadcast:
            signal_bus.active_rule_changed.emit(rule["id"])
        if show_notice:
            InfoBar.success(
                title=tr("Default rule updated", "默认规则已更新", "既定ルールを更新しました"),
                content=f"{_rule_display_name(rule)} · {_rule_summary(normalized)}",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=1900,
                parent=self.window(),
            )
        return True

    def apply_selected(self, show_notice: bool = False) -> bool:
        return self._activate_rule(self.selected_rule_id(), show_notice=show_notice, broadcast=True)


class RulesInterface(QWidget):
    """Manage named filter/download presets."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("RulesInterface")
        self._selected_id = ""
        self._draft_rule: dict[str, Any] | None = None
        self._rules_cache: list[dict[str, Any]] = []
        self._build_ui()
        self._reload_list(active_rule_id())
        signal_bus.active_rule_changed.connect(self._on_active_rule_changed)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 22, 28, 18)
        root.setSpacing(12)
        root.addWidget(TitleLabel(tr("Download Rules", "下载规则", "ダウンロードルール"), self))
        intro = BodyLabel(tr(
            "One rule combines filters, naming and download behavior. Selecting a rule on a download page applies it immediately.",
            "一个规则统一保存筛选、命名和下载行为；在下载页选择后会立即生效并成为默认规则。",
            "フィルター、命名、保存動作を一つにまとめ、選択時に即座に適用します。",
        ), self)
        intro.setWordWrap(True)
        root.addWidget(intro)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self._splitter = splitter
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(10)
        _style_rule_splitter(splitter)
        root.addWidget(splitter, 1)

        left = CardWidget(splitter)
        left.setMinimumWidth(300)
        left.setMaximumWidth(460)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(14, 14, 14, 14)
        left_layout.setSpacing(10)
        left_layout.addWidget(SubtitleLabel(tr("Rules", "规则列表", "ルール一覧"), left))
        hint = BodyLabel(tr("The teal dot marks the current default.", "青绿色圆点表示当前默认规则。", "青緑の点は現在の既定ルールです。"), left)
        hint.setWordWrap(True)
        left_layout.addWidget(hint)
        self._list = QListWidget(left)
        self._list.setSpacing(5)
        self._list.setWordWrap(True)
        self._list.setStyleSheet(self._list_style())
        self._list.currentItemChanged.connect(self._on_rule_selected)
        left_layout.addWidget(self._list, 1)
        actions = QGridLayout()
        self._new_btn = PrimaryPushButton(tr("New", "新建", "新規"), left, FluentIcon.ADD)
        self._duplicate_btn = PushButton(tr("Duplicate", "复制", "複製"), left)
        self._delete_btn = PushButton(tr("Delete", "删除", "削除"), left)
        self._new_btn.clicked.connect(self._new_rule)
        self._duplicate_btn.clicked.connect(self._duplicate_rule)
        self._delete_btn.clicked.connect(self._delete_rule)
        actions.addWidget(self._new_btn, 0, 0)
        actions.addWidget(self._duplicate_btn, 0, 1)
        actions.addWidget(self._delete_btn, 0, 2)
        left_layout.addLayout(actions)
        splitter.addWidget(left)

        right = QWidget(splitter)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(10)
        self._form_scroll = QScrollArea(right)
        self._form_scroll.setWidgetResizable(True)
        self._form_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._form_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._form = RuleFormWidget()
        self._form.name_edit.textChanged.connect(self._on_form_name_changed)
        self._form_scroll.setWidget(self._form)
        right_layout.addWidget(self._form_scroll, 1)
        bottom = QHBoxLayout()
        self._apply_btn = PrimaryPushButton(tr("Set as default", "设为当前默认", "既定に設定"), right, FluentIcon.ACCEPT)
        self._save_btn = PrimaryPushButton(tr("Save rule", "保存规则", "ルールを保存"), right, FluentIcon.SAVE)
        self._apply_btn.clicked.connect(self._apply_current)
        self._save_btn.clicked.connect(self._save_rule)
        bottom.addStretch()
        bottom.addWidget(self._apply_btn)
        bottom.addWidget(self._save_btn)
        right_layout.addLayout(bottom)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([360, 1120])
        self._apply_fluent_scrollbars()

    def _list_style(self) -> str:
        if isDarkTheme():
            return "QListWidget{background:transparent;border:none;} QListWidget::item{padding:10px;border:1px solid #3c434a;border-radius:8px;color:#eef2f5;} QListWidget::item:selected{border:1px solid #18a8b2;background:transparent;}"
        return "QListWidget{background:transparent;border:none;} QListWidget::item{padding:10px;border:1px solid #e2e6ea;border-radius:8px;color:#202428;} QListWidget::item:selected{border:1px solid #00a4af;background:transparent;color:#15272a;}"

    def _reload_list(self, select_id: str | None = None):
        saved_rules = rule_store.list_available()
        self._rules_cache = list(saved_rules)
        if self._draft_rule:
            self._rules_cache.insert(1 if self._rules_cache else 0, self._draft_rule)
        active_id = active_rule_id()
        self._list.blockSignals(True)
        self._list.clear()
        for rule in self._rules_cache:
            active_mark = "● " if rule["id"] == active_id else "  "
            if rule["id"] == DRAFT_RULE_ID:
                kind = tr("Unsaved draft", "未保存草稿", "未保存の下書き")
            else:
                kind = tr("Built-in default", "内置默认", "内蔵の既定") if rule.get("builtin") else tr("Saved rule", "已保存规则", "保存済み")
            item = QListWidgetItem(f"{active_mark}{_rule_display_name(rule)}\n{kind} · {_rule_summary(rule['payload'])}")
            item.setData(Qt.ItemDataRole.UserRole, rule["id"])
            item.setSizeHint(QSize(0, 72))
            item.setToolTip(str(rule["payload"].get("filename_template", "")))
            self._list.addItem(item)
        self._list.blockSignals(False)
        target = select_id or active_id
        for index in range(self._list.count()):
            if self._list.item(index).data(Qt.ItemDataRole.UserRole) == target:
                self._list.setCurrentRow(index)
                self._paint_list_items()
                return
        if self._list.count():
            self._list.setCurrentRow(0)
        self._paint_list_items()

    def _paint_list_items(self):
        dark = isDarkTheme()
        normal = QColor("#292d32" if dark else "#fafbfc")
        default_bg = QColor("#21433f" if dark else "#e8f6ec")
        selected_bg = QColor("#203d53" if dark else "#e7f4ff")
        draft_bg = QColor("#4a3d25" if dark else "#fff4dc")
        selected_default_bg = QColor("#204c54" if dark else "#d9eef6")
        for index in range(self._list.count()):
            item = self._list.item(index)
            rule_id = str(item.data(Qt.ItemDataRole.UserRole) or "")
            is_selected = item is self._list.currentItem()
            if rule_id == DRAFT_RULE_ID:
                color = draft_bg
            elif rule_id == active_rule_id() and is_selected:
                color = selected_default_bg
            elif rule_id == active_rule_id():
                color = default_bg
            elif is_selected:
                color = selected_bg
            else:
                color = normal
            item.setBackground(QBrush(color))

    def _on_rule_selected(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None):
        if current is None:
            return
        rule_id = str(current.data(Qt.ItemDataRole.UserRole) or "")
        rule = next((r for r in self._rules_cache if r["id"] == rule_id), None)
        if not rule:
            return
        self._selected_id = rule_id
        builtin = bool(rule.get("builtin"))
        self._form.set_builtin_mode(builtin)
        name = tr("Default Download (built in)", "默认下载（内置）", "既定ダウンロード（内蔵）") if builtin else rule["name"]
        self._form.name_edit.setText(name)
        self._form.load_payload(rule["payload"])
        self._save_btn.setEnabled(not builtin)
        self._delete_btn.setEnabled(not builtin)
        self._paint_list_items()

    def _on_form_name_changed(self, name: str):
        if self._selected_id != DRAFT_RULE_ID or not self._draft_rule:
            return
        display_name = name.strip() or tr("New Rule", "新规则", "新規ルール")
        self._draft_rule["name"] = display_name
        for index in range(self._list.count()):
            item = self._list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == DRAFT_RULE_ID:
                item.setText(f"  {display_name}\n{tr('Unsaved draft', '未保存草稿', '未保存の下書き')} · {_rule_summary(self._draft_rule['payload'])}")
                break
        self._paint_list_items()

    def _new_rule(self):
        self._draft_rule = {
            "id": DRAFT_RULE_ID,
            "name": tr("New Rule", "新规则", "新規ルール"),
            "created_at": "",
            "updated_at": "",
            "builtin": False,
            "payload": current_rule_payload(),
        }
        self._selected_id = DRAFT_RULE_ID
        self._form.set_builtin_mode(False)
        self._form.name_edit.setText(self._draft_rule["name"])
        self._form.load_payload(self._draft_rule["payload"])
        self._save_btn.setEnabled(True)
        self._delete_btn.setEnabled(True)
        self._reload_list(DRAFT_RULE_ID)
        self._form.name_edit.setFocus()

    def _duplicate_rule(self):
        try:
            payload = self._form.payload()
        except ValueError as exc:
            self._show_error(str(exc))
            return
        base = (
            self._form.name_edit.text()
            .replace("（内置）", "")
            .replace("（内蔵）", "")
            .replace(" (built in)", "")
            .strip()
        )
        self._draft_rule = {
            "id": DRAFT_RULE_ID,
            "name": (base or tr("New Rule", "新规则", "新規ルール"))
            + tr(" copy", " 副本", " コピー"),
            "created_at": "",
            "updated_at": "",
            "builtin": False,
            "payload": payload,
        }
        self._selected_id = DRAFT_RULE_ID
        self._form.set_builtin_mode(False)
        self._form.name_edit.setText(self._draft_rule["name"])
        self._form.load_payload(payload)
        self._save_btn.setEnabled(True)
        self._delete_btn.setEnabled(True)
        self._reload_list(DRAFT_RULE_ID)

    def _save_rule(self):
        try:
            payload = self._form.payload()
            old_id = self._selected_id if self._selected_id != DRAFT_RULE_ID else ""
            rule = rule_store.save(self._form.name_edit.text(), payload, old_id or None)
        except ValueError as exc:
            self._show_error(str(exc))
            return
        self._draft_rule = None
        self._selected_id = rule["id"]
        if active_rule_id() == old_id:
            apply_rule_payload(rule["payload"])
            signal_bus.download_options_changed.emit()
            signal_bus.active_rule_changed.emit(rule["id"])
        signal_bus.rules_changed.emit()
        self._reload_list(rule["id"])
        InfoBar.success(title=tr("Rule saved", "规则已保存", "ルールを保存しました"), content=_rule_display_name(rule), orient=Qt.Orientation.Horizontal, isClosable=True, position=InfoBarPosition.TOP, duration=1800, parent=self)

    def _delete_rule(self):
        if self._selected_id == DRAFT_RULE_ID:
            self._draft_rule = None
            self._selected_id = active_rule_id()
            self._reload_list(self._selected_id)
            return
        if not self._selected_id or self._selected_id == BUILTIN_DEFAULT_RULE_ID:
            return
        if not show_fluent_confirmation(
            self,
            tr("Delete rule", "删除规则", "ルールを削除"),
            tr("Delete the selected rule?", "确定删除当前规则？", "選択したルールを削除しますか？"),
            yes_text=tr("Delete", "删除", "削除"),
            no_text=tr("Cancel", "取消", "キャンセル"),
        ):
            return
        deleted_active = active_rule_id() == self._selected_id
        if rule_store.delete(self._selected_id):
            self._selected_id = BUILTIN_DEFAULT_RULE_ID if deleted_active else ""
            if deleted_active:
                set_active_rule_id(BUILTIN_DEFAULT_RULE_ID)
                apply_rule_payload(default_rule_payload())
                signal_bus.active_rule_changed.emit(BUILTIN_DEFAULT_RULE_ID)
                signal_bus.download_options_changed.emit()
            signal_bus.rules_changed.emit()
            self._reload_list(self._selected_id or None)

    def _apply_current(self):
        if self._selected_id == DRAFT_RULE_ID:
            InfoBar.warning(title=tr("Save first", "请先保存规则", "先に保存してください"), content=tr("Save the new rule before making it default.", "新规则保存后才能设为默认。", "新規ルールは保存後に既定へ設定できます。"), orient=Qt.Orientation.Horizontal, isClosable=True, position=InfoBarPosition.TOP, duration=2200, parent=self)
            return
        if not self._selected_id:
            InfoBar.warning(title=tr("Save first", "请先保存规则", "先に保存してください"), content=tr("Save the new rule before making it default.", "新规则保存后才能设为默认。", "新規ルールは保存後に既定へ設定できます。"), orient=Qt.Orientation.Horizontal, isClosable=True, position=InfoBarPosition.TOP, duration=2200, parent=self)
            return
        try:
            payload = self._form.payload()
        except ValueError as exc:
            self._show_error(str(exc))
            return
        apply_rule_payload(payload)
        set_active_rule_id(self._selected_id)
        signal_bus.download_options_changed.emit()
        signal_bus.active_rule_changed.emit(self._selected_id)
        self._reload_list(self._selected_id)
        InfoBar.success(title=tr("Default rule updated", "默认规则已更新", "既定ルールを更新しました"), content=self._form.name_edit.text(), orient=Qt.Orientation.Horizontal, isClosable=True, position=InfoBarPosition.TOP, duration=1800, parent=self)

    def _on_active_rule_changed(self, rule_id: str):
        self._reload_list(self._selected_id or rule_id)

    def _show_error(self, content: str):
        InfoBar.error(title=tr("Invalid rule", "规则无效", "ルールが無効"), content=content, orient=Qt.Orientation.Horizontal, isClosable=True, position=InfoBarPosition.TOP, duration=2500, parent=self)

    def _apply_fluent_scrollbars(self):
        handle = "#6f7d89" if isDarkTheme() else "#9aa7b2"
        hover = "#22c3cf" if isDarkTheme() else "#00a4af"
        qss = f"""
        QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px 1px 2px 1px; }}
        QScrollBar::handle:vertical {{ background: {handle}; min-height: 38px; border-radius: 5px; }}
        QScrollBar::handle:vertical:hover {{ background: {hover}; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
        QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 1px 2px 1px 2px; }}
        QScrollBar::handle:horizontal {{ background: {handle}; min-width: 38px; border-radius: 5px; }}
        QScrollBar::handle:horizontal:hover {{ background: {hover}; }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0px; }}
        """
        self._list.setStyleSheet(self._list_style())
        for scroll_area in (self._list, self._form_scroll):
            scroll_area.verticalScrollBar().setStyleSheet(qss)
            scroll_area.horizontalScrollBar().setStyleSheet(qss)
        # Styling the whole QScrollArea makes its viewport inherit an opaque
        # light palette in dark mode. Keep the viewport transparent instead.
        surface = "#202020" if isDarkTheme() else "#f7f8fa"
        self.setStyleSheet(f"QWidget#RulesInterface {{ background-color: {surface}; }}")
        self._form_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self._form_scroll.viewport().setStyleSheet(f"background-color: {surface};")
        self._form_scroll.viewport().setAutoFillBackground(True)
        self._form.setStyleSheet(f"background-color: {surface};")

    def refresh_theme_styles(self):
        if hasattr(self, "_splitter"):
            _style_rule_splitter(self._splitter)
        self._list.setStyleSheet(self._list_style())
        self._reload_list(self._selected_id or None)
        self._apply_fluent_scrollbars()
