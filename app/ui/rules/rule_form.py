"""Editor form for one download rule: filters, naming and download behavior."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from PySide6.QtCore import QPoint, QTimer, Qt
from PySide6.QtWidgets import QFormLayout, QGridLayout, QVBoxLayout, QWidget

from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    FluentIcon,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    PushButton,
    SubtitleLabel,
    SwitchButton,
)

from ...core.rules import default_rule_payload, normalize_rule_payload
from ...core.download.paths import validate_filename_template
from ...core.download.template_builder import preview_template
from ...core.manager import download_manager
from ...i18n import tr
from ..theme import set_secondary_text
from .rule_text import _rule_summary
from .tag_popup import _RuleTagSuggestionPopup
from .template_builder import FilenameTemplateBuilder


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

        root.addWidget(self._build_identity_card())
        root.addWidget(self._build_storage_card())
        root.addWidget(self._build_filter_card())
        root.addWidget(self._build_title_card())
        root.addWidget(self._build_download_card())
        root.addStretch(1)

    def _build_identity_card(self) -> CardWidget:
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
        return identity

    def _build_storage_card(self) -> CardWidget:
        storage_card = CardWidget(self)
        storage_layout = QGridLayout(storage_card)
        storage_layout.setContentsMargins(18, 14, 18, 14)
        storage_layout.setHorizontalSpacing(10)
        storage_layout.setVerticalSpacing(10)
        storage_layout.addWidget(SubtitleLabel(tr("Filename template", "下载命名规则", "ファイル名テンプレート"), storage_card), 0, 0, 1, 4)
        self.template_builder = FilenameTemplateBuilder(storage_card)
        storage_layout.addWidget(self.template_builder, 1, 0, 1, 4)
        storage_layout.addWidget(
            BodyLabel(tr("Full template (editable)", "完整规则（可直接编辑）", "完全なテンプレート（直接編集可）"), storage_card),
            2, 0, 1, 4,
        )
        self.filename_template_edit = LineEdit(storage_card)
        self.filename_template_edit.setPlaceholderText("{author}/{YYYY-MM-DD}_{title}_{id}.mp4")
        storage_layout.addWidget(self.filename_template_edit, 3, 0, 1, 3)
        self.template_preview_label = BodyLabel("", storage_card)
        self.template_preview_label.setWordWrap(True)
        storage_layout.addWidget(self.template_preview_label, 4, 0, 1, 4)
        self.template_builder.template_changed.connect(self.filename_template_edit.setText)
        self.filename_template_edit.textChanged.connect(self._on_template_text_changed)
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
        storage_layout.addWidget(self.validate_template_btn, 3, 3)
        template_help = BodyLabel(
            tr(
                "Available: {author} {username} {title} {id} {YYYY-MM-DD} {YYYY} {MM} {DD} {quality} {views} {likes} {comments} {duration} {slug} {rating} {date}",
                "可用占位符：{author} {username} {title} {id} {YYYY-MM-DD} {YYYY} {MM} {DD} {quality} {views} {likes} {comments} {duration} {slug} {rating} {date}",
                "使用可能: {author} {username} {title} {id} {YYYY-MM-DD} {YYYY} {MM} {DD} {quality} {views} {likes} {comments} {duration} {slug} {rating} {date}",
            ),
            storage_card,
        )
        template_help.setWordWrap(True)
        set_secondary_text(template_help)
        storage_layout.addWidget(template_help, 5, 0, 1, 4)
        self._on_template_text_changed(self.filename_template_edit.text())
        return storage_card

    def _on_template_text_changed(self, text: str):
        self.template_builder.set_template(text)
        shown = preview_template(text)
        self.template_preview_label.setText(
            tr("Preview: ", "预览：", "プレビュー: ") + shown
            if shown
            else tr("Preview: (template is invalid)", "预览：（规则无效）", "プレビュー: （テンプレートが無効です）")
        )

    def _build_filter_card(self) -> CardWidget:
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
        set_secondary_text(filter_hint)
        filter_layout.addWidget(filter_hint, 7, 0, 1, 4)
        return filter_card

    def _build_title_card(self) -> CardWidget:
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
        return title_card

    def _build_download_card(self) -> CardWidget:
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
        set_secondary_text(self.record_history_hint)
        download_layout.addWidget(
            self.record_history_hint,
            3,
            2,
            1,
            2,
        )
        self.record_history.checkedChanged.connect(self._update_record_history_hint)
        self._update_record_history_hint()
        return download_card

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

    _DRAFT_TEXTS = (
        "name_edit", "filename_template_edit", "likes_edit", "views_edit", "start_edit",
        "end_edit", "include_tags_edit", "exclude_tags_edit", "title_include_edit", "title_exclude_edit",
    )
    _DRAFT_SWITCHES = (
        "filter_enabled", "likes_enabled", "views_enabled", "date_enabled", "include_tags_enabled",
        "exclude_tags_enabled", "download_video", "mark_only", "download_thumb", "collect_nfo", "record_history",
    )

    def raw_state(self) -> dict[str, Any]:
        """Every field exactly as typed, valid or not (for carrying an edit over)."""

        state: dict[str, Any] = {name: getattr(self, name).text() for name in self._DRAFT_TEXTS}
        state.update({name: getattr(self, name).isChecked() for name in self._DRAFT_SWITCHES})
        return state

    def load_raw_state(self, state: dict[str, Any]):
        self._loading_payload = True
        try:
            for name in self._DRAFT_SWITCHES:
                if name in state:
                    getattr(self, name).setChecked(bool(state[name]))
            for name in self._DRAFT_TEXTS:
                if name in state:
                    getattr(self, name).setText(str(state[name]))
        finally:
            self._loading_payload = False
        self._hide_tag_suggestions()
        self._update_record_history_hint()

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
