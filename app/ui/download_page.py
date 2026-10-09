"""Download Interface — URL input + operation log."""
from __future__ import annotations

from datetime import datetime

import re

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    CheckBox,
    FluentIcon,
    HyperlinkButton,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    PlainTextEdit,
    MessageBoxBase,
    PrimaryPushButton,
    PushButton,
    SubtitleLabel,
    SwitchButton,
    TitleLabel,
    ToolButton,
    TransparentToolButton,
    isDarkTheme,
)

from ..config import app_config
from ..core.manager import download_manager
from ..i18n import tr
from ..signal_bus import signal_bus
from .rules_page import RulePicker
from .task_page import TaskCenterInterface
from .theme import (
    CARD_MARGINS,
    PAGE_MARGINS,
    PAGE_SPACING,
    FluentSplitter,
    apply_scrollbars,
    palette,
    set_secondary_text,
    set_status_text,
)

_URL_SPLIT_RE = re.compile(r"[\s,，；;]+")


def split_submitted_urls(text: str) -> list[str]:
    """Split pasted text into unique http(s) URLs, keeping their order.

    A single non-URL token is returned unchanged so the resolver can still
    report a precise error for it.
    """
    tokens = [token.strip() for token in _URL_SPLIT_RE.split(str(text or "")) if token.strip()]
    urls: list[str] = []
    for token in tokens:
        if token.lower().startswith(("http://", "https://")) and token not in urls:
            urls.append(token)
    if urls:
        return urls
    stripped = str(text or "").strip()
    return [stripped] if stripped else []


class _SubscriptionPromptDialog(MessageBoxBase):
    """Fluent three-choice prompt used after downloading an author or playlist."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.choice = "once"
        self.title_label = SubtitleLabel(
            tr("Add Subscription", "加入订阅列表", "購読に追加"), self
        )
        self.content_label = BodyLabel(
            tr(
                "Add this author/playlist to the local subscription list?",
                "是否把这个作者/播放列表加入本地订阅列表？",
                "この作者/プレイリストをローカル購読に追加しますか？",
            ),
            self,
        )
        self.content_label.setWordWrap(True)
        self.no_remind = CheckBox(
            tr("Do not ask again", "下次不再提醒", "次回から確認しない"), self
        )

        self.viewLayout.addWidget(self.title_label)
        self.viewLayout.addWidget(self.content_label)
        self.viewLayout.addWidget(self.no_remind)
        self.yesButton.setText(tr("Add This Time", "本次加入", "今回追加"))
        self.cancelButton.setText(tr("No", "不加入", "追加しない"))
        self.always_button = PushButton(
            tr("Always Add", "以后都自动加入", "常に追加"), self.buttonGroup
        )
        self.buttonLayout.insertWidget(
            1, self.always_button, 1, Qt.AlignmentFlag.AlignVCenter
        )
        self.always_button.clicked.connect(self._accept_always)
        self.widget.setMinimumWidth(620)

    def _accept_always(self):
        self.choice = "always"
        self.accept()


def option_button_style(checked: bool) -> str:
    """Return a theme-aware style for the legacy download option toggles."""
    p = palette()
    if checked:
        background, foreground, border = p.accent, ("#000000" if isDarkTheme() else "#ffffff"), p.accent
    else:
        background, foreground, border = p.surface, p.text, p.border
    return f"""
QPushButton {{
    background-color: {background};
    color: {foreground};
    border: 1px solid {border};
    border-radius: 6px;
    padding: 8px 10px;
}}
QPushButton:hover {{ background-color: {p.hover if not checked else background}; }}
"""


class FilterDialog(QDialog):
    """Simple filter configuration dialog for likes/date/views."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(tr("Filter Rules", "筛选条件", "フィルター条件"))
        self.setModal(True)
        self.setMinimumWidth(520)
        self._build_ui()
        self._load()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        enable_row = QHBoxLayout()
        enable_row.addWidget(
            BodyLabel(tr("Enable filters", "启用筛选", "フィルターを有効化"), self)
        )
        self._filter_enabled_switch = SwitchButton(self)
        enable_row.addWidget(self._filter_enabled_switch)
        enable_row.addStretch()
        layout.addLayout(enable_row)

        likes_row = QHBoxLayout()
        likes_row.addWidget(
            BodyLabel(tr("Enable likes filter", "启用点赞筛选", "いいね数フィルターを有効化"), self)
        )
        self._likes_switch = SwitchButton(self)
        likes_row.addWidget(self._likes_switch)
        likes_row.addWidget(BodyLabel(tr("Likes >=", "点赞数 >=", "いいね数 >="), self))
        self._likes_edit = LineEdit(self)
        self._likes_edit.setPlaceholderText("0")
        likes_row.addWidget(self._likes_edit)
        layout.addLayout(likes_row)

        views_row = QHBoxLayout()
        views_row.addWidget(
            BodyLabel(tr("Enable views filter", "启用播放筛选", "再生数フィルターを有効化"), self)
        )
        self._views_switch = SwitchButton(self)
        views_row.addWidget(self._views_switch)
        views_row.addWidget(BodyLabel(tr("Views >=", "播放数 >=", "再生数 >="), self))
        self._views_edit = LineEdit(self)
        self._views_edit.setPlaceholderText("0")
        views_row.addWidget(self._views_edit)
        layout.addLayout(views_row)

        date_row = QHBoxLayout()
        date_row.addWidget(
            BodyLabel(tr("Enable date filter", "启用日期筛选", "日付フィルターを有効化"), self)
        )
        self._date_switch = SwitchButton(self)
        date_row.addWidget(self._date_switch)
        date_row.addWidget(BodyLabel(tr("Start YYYY-MM-DD", "起始 YYYY-MM-DD", "開始 YYYY-MM-DD"), self))
        self._start_edit = LineEdit(self)
        self._start_edit.setPlaceholderText("1970-01-01")
        date_row.addWidget(self._start_edit)
        date_row.addWidget(BodyLabel(tr("End YYYY-MM-DD", "结束 YYYY-MM-DD", "終了 YYYY-MM-DD"), self))
        self._end_edit = LineEdit(self)
        self._end_edit.setPlaceholderText(datetime.now().strftime("%Y-%m-%d"))
        date_row.addWidget(self._end_edit)
        layout.addLayout(date_row)

        include_row = QHBoxLayout()
        include_row.addWidget(
            BodyLabel(
                tr("Enable include tags", "启用包含标签", "包含タグを有効化"),
                self,
            )
        )
        self._include_tags_switch = SwitchButton(self)
        include_row.addWidget(self._include_tags_switch)
        include_row.addWidget(
            BodyLabel(
                tr("Tags list", "标签列表", "タグ一覧"),
                self,
            )
        )
        self._include_tags_edit = LineEdit(self)
        self._include_tags_edit.setPlaceholderText(
            tr("e.g. 2d,mmd", "例如 2d,mmd", "例: 2d,mmd")
        )
        include_row.addWidget(self._include_tags_edit)
        layout.addLayout(include_row)

        exclude_row = QHBoxLayout()
        exclude_row.addWidget(
            BodyLabel(
                tr("Enable exclude tags", "启用排除标签", "除外タグを有効化"),
                self,
            )
        )
        self._exclude_tags_switch = SwitchButton(self)
        exclude_row.addWidget(self._exclude_tags_switch)
        exclude_row.addWidget(BodyLabel(tr("Tags list", "标签列表", "タグ一覧"), self))
        self._exclude_tags_edit = LineEdit(self)
        self._exclude_tags_edit.setPlaceholderText(
            tr("e.g. vr,ai", "例如 vr,ai", "例: vr,ai")
        )
        exclude_row.addWidget(self._exclude_tags_edit)
        layout.addLayout(exclude_row)

        tips = BodyLabel(
            tr(
                "Each filter can be enabled independently. Empty end date means today.",
                "筛选项可单独启用。结束日期留空表示今天。",
                "各フィルターは個別に有効化できます。終了日が空なら当日扱いです。",
            ),
            self,
        )
        layout.addWidget(tips)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = PrimaryPushButton(tr("Cancel", "取消", "キャンセル"), self)
        save_btn = PrimaryPushButton(tr("Save", "保存", "保存"), self)
        cancel_btn.clicked.connect(self.reject)
        save_btn.clicked.connect(self._save_and_accept)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

    def _load(self):
        self._filter_enabled_switch.setChecked(app_config.filter_enabled)
        self._likes_switch.setChecked(app_config.filter_min_likes_enabled)
        self._views_switch.setChecked(app_config.filter_min_views_enabled)
        self._date_switch.setChecked(app_config.filter_date_enabled)
        self._likes_edit.setText(str(app_config.filter_min_likes))
        self._views_edit.setText(str(app_config.filter_min_views))
        self._start_edit.setText(app_config.filter_start_date or "1970-01-01")
        self._end_edit.setText(app_config.filter_end_date or datetime.now().strftime("%Y-%m-%d"))
        self._include_tags_switch.setChecked(app_config.filter_include_tags_enabled)
        self._include_tags_edit.setText(app_config.filter_include_tags or "")
        self._exclude_tags_switch.setChecked(app_config.filter_exclude_tags_enabled)
        self._exclude_tags_edit.setText(app_config.filter_exclude_tags or "")

    def _save_and_accept(self):
        try:
            likes = int((self._likes_edit.text() or "0").strip())
            views = int((self._views_edit.text() or "0").strip())
            if likes < 0 or views < 0:
                raise ValueError(
                    tr(
                        "Likes/views cannot be negative",
                        "点赞/播放不能为负数",
                        "いいね数/再生数は負数にできません",
                    )
                )

            start = (self._start_edit.text() or "1970-01-01").strip()
            end = (self._end_edit.text() or "").strip()
            datetime.strptime(start, "%Y-%m-%d")
            if end:
                datetime.strptime(end, "%Y-%m-%d")
        except Exception as exc:
            InfoBar.error(
                title=tr("Invalid filter values", "筛选参数无效", "フィルター値が不正です"),
                content=str(exc),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=2500,
                parent=self,
            )
            return

        app_config.filter_enabled = self._filter_enabled_switch.isChecked()
        app_config.filter_min_likes_enabled = self._likes_switch.isChecked()
        app_config.filter_min_views_enabled = self._views_switch.isChecked()
        app_config.filter_date_enabled = self._date_switch.isChecked()
        app_config.filter_min_likes = likes
        app_config.filter_min_views = views
        app_config.filter_start_date = start
        app_config.filter_end_date = end
        app_config.filter_include_tags_enabled = self._include_tags_switch.isChecked()
        app_config.filter_include_tags = self._include_tags_edit.text().strip()
        app_config.filter_exclude_tags_enabled = self._exclude_tags_switch.isChecked()
        app_config.filter_exclude_tags = self._exclude_tags_edit.text().strip()
        self.accept()


# ── Download Interface ────────────────────────────────────────────────────────

class DownloadInterface(QWidget):
    """Download workbench: URL input, runtime log, and task table."""

    _MAX_LOG_BLOCKS = 2000

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("DownloadInterface")
        self._pending_logs: list[str] = []
        self._syncing_options = False
        self._log_flush_timer = QTimer(self)
        self._log_flush_timer.setInterval(200)
        self._log_flush_timer.timeout.connect(self._flush_logs)

        self._build_ui()
        signal_bus.log_message.connect(self._append_log)
        signal_bus.login_state_changed.connect(self._on_login_state)
        signal_bus.download_options_changed.connect(self._sync_option_controls)
        self._on_login_state(bool(download_manager.api.token))
        self._sync_option_controls()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(*PAGE_MARGINS)
        root.setSpacing(PAGE_SPACING)

        title_row = QHBoxLayout()
        title_row.setSpacing(12)
        title_row.addWidget(TitleLabel(tr("Download Workbench", "下载工作台", "ダウンロードワークベンチ"), self))
        title_row.addStretch()
        # Login state lives beside the title as a compact status line instead
        # of a full-width card that truncated its own text.
        self._login_status_lbl = BodyLabel("", self)
        self._login_status_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        title_row.addWidget(self._login_status_lbl)
        self._login_settings_btn = HyperlinkButton(self)
        self._login_settings_btn.setText(tr("Sign in", "去登录", "ログイン"))
        self._login_settings_btn.clicked.connect(self._open_settings_page)
        title_row.addWidget(self._login_settings_btn)
        root.addLayout(title_row)

        splitter = FluentSplitter(Qt.Orientation.Horizontal, self)
        self._splitter = splitter
        splitter.setChildrenCollapsible(False)
        root.addWidget(splitter, stretch=1)

        left_panel = QWidget(self)
        left_panel.setMinimumWidth(300)
        left_panel.setMaximumWidth(520)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(PAGE_SPACING)
        splitter.addWidget(left_panel)

        right_panel = QWidget(self)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([380, 1180])

        # Download behavior is now controlled by the selected named rule.
        # Keep the legacy controls hidden for compatibility with older signals and
        # integrations, but do not expose four competing switches on this page.
        self._download_video_btn = self._make_option_button(
            tr("Download Video", "下载视频", "動画を保存"), "", left_panel, FluentIcon.DOWNLOAD
        )
        self._mark_downloaded_btn = self._make_option_button(
            tr("Mark Only", "仅标记已下载", "マークのみ"), "", left_panel, FluentIcon.CHECKBOX
        )
        self._download_thumb_btn = self._make_option_button(
            tr("Thumbnail", "下载封面", "サムネイル"), "", left_panel, FluentIcon.PHOTO
        )
        self._collect_nfo_btn = self._make_option_button(
            "NFO", "", left_panel, FluentIcon.DOCUMENT
        )
        for legacy_button in (
            self._download_video_btn,
            self._mark_downloaded_btn,
            self._download_thumb_btn,
            self._collect_nfo_btn,
        ):
            legacy_button.hide()
        self._download_video_btn.clicked.connect(self._on_download_video_clicked)
        self._mark_downloaded_btn.clicked.connect(self._on_mark_downloaded_clicked)
        self._download_thumb_btn.clicked.connect(self._on_download_thumb_clicked)
        self._collect_nfo_btn.clicked.connect(self._on_collect_nfo_clicked)

        # ── URL input card ────────────────────────────────────────────────────
        url_card = CardWidget(left_panel)
        url_layout = QVBoxLayout(url_card)
        url_layout.setContentsMargins(*CARD_MARGINS)
        url_layout.setSpacing(10)

        url_layout.addWidget(SubtitleLabel(tr("Target URL", "目标地址", "対象URL"), url_card))
        url_hint = BodyLabel(
            tr(
                "Video, profile, playlist and API search URLs. Paste several links at once to queue them together.",
                "支持单视频、作者主页、播放列表和 API 搜索链接；可一次粘贴多条链接批量加入。",
                "動画/プロフィール/プレイリスト/API検索URLに対応。複数のリンクをまとめて貼り付けできます。",
            ),
            url_card,
        )
        url_hint.setWordWrap(True)
        set_secondary_text(url_hint)
        url_layout.addWidget(url_hint)

        url_row = QHBoxLayout()
        url_row.setSpacing(8)
        self._url_edit = LineEdit(url_card)
        self._url_edit.setPlaceholderText(
            tr(
                "Paste a video, profile, playlist or API URL…",
                "粘贴视频、作者主页、播放列表或 API 链接…",
                "動画・プロフィール・プレイリスト・API URLを貼り付け…",
            )
        )
        self._url_edit.setClearButtonEnabled(True)
        self._url_edit.returnPressed.connect(self._submit)
        url_row.addWidget(self._url_edit, 1)
        self._paste_btn = ToolButton(FluentIcon.PASTE, url_card)
        self._paste_btn.setToolTip(
            tr(
                "Paste links from the clipboard (Ctrl+Shift+V submits directly)",
                "从剪贴板粘贴链接（Ctrl+Shift+V 直接提交）",
                "クリップボードから貼り付け（Ctrl+Shift+V で直接送信）",
            )
        )
        self._paste_btn.clicked.connect(self._paste_from_clipboard)
        url_row.addWidget(self._paste_btn)
        url_layout.addLayout(url_row)

        rule_row = QHBoxLayout()
        rule_row.setSpacing(8)
        rule_label = BodyLabel(tr("Rule", "下载规则", "ルール"), url_card)
        set_secondary_text(rule_label)
        rule_row.addWidget(rule_label)
        self._rule_picker = RulePicker(url_card)
        self._rule_picker.combo.setMinimumWidth(160)
        rule_row.addWidget(self._rule_picker, 1)
        url_layout.addLayout(rule_row)

        self._submit_btn = PrimaryPushButton(
            tr("Download", "下载", "ダウンロード"),
            url_card,
            FluentIcon.DOWNLOAD,
        )
        self._submit_btn.clicked.connect(self._submit)
        url_layout.addWidget(self._submit_btn)
        left_layout.addWidget(url_card)

        # ── Operation log card ────────────────────────────────────────────────
        log_card = CardWidget(left_panel)
        log_card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        log_layout = QVBoxLayout(log_card)
        log_layout.setContentsMargins(*CARD_MARGINS)
        log_layout.setSpacing(8)

        log_header = QHBoxLayout()
        log_header.addWidget(SubtitleLabel(tr("Runtime Log", "运行日志", "実行ログ"), log_card))
        log_header.addStretch()
        copy_log_btn = TransparentToolButton(FluentIcon.COPY, log_card)
        copy_log_btn.setToolTip(tr("Copy log", "复制日志", "ログをコピー"))
        copy_log_btn.clicked.connect(self._copy_log)
        log_header.addWidget(copy_log_btn)
        clear_log_btn = TransparentToolButton(FluentIcon.DELETE, log_card)
        clear_log_btn.setToolTip(tr("Clear log", "清空日志", "ログをクリア"))
        clear_log_btn.clicked.connect(self._clear_log)
        log_header.addWidget(clear_log_btn)
        log_layout.addLayout(log_header)

        self._log_edit = PlainTextEdit(log_card)
        self._log_edit.setReadOnly(True)
        self._log_edit.setMaximumBlockCount(self._MAX_LOG_BLOCKS)
        apply_scrollbars(self._log_edit)
        mono = QFont("Consolas", 9)
        if not mono.exactMatch():
            mono = QFont("Courier New", 9)
        self._log_edit.setFont(mono)
        self._log_edit.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self._log_edit.setMinimumHeight(140)
        self._log_edit.setPlaceholderText(
            tr("Logs will appear here…", "操作日志将显示在此…", "ログはここに表示されます…")
        )
        log_layout.addWidget(self._log_edit)
        left_layout.addWidget(log_card, stretch=1)

        self._task_center = TaskCenterInterface(right_panel, embedded=True)
        right_layout.addWidget(self._task_center, stretch=1)

    def _make_option_button(self, text: str, tooltip: str, parent: QWidget, _icon: FluentIcon) -> PrimaryPushButton:
        button = PrimaryPushButton(text, parent)
        button._base_text = text
        button.setCheckable(True)
        button.setMinimumHeight(48)
        button.setToolTip(tooltip)
        return button

    # ── Slots ─────────────────────────────────────────────────────────────────

    # ── language rebuild ────────────────────────────────────────────────────

    def draft_state(self) -> dict | None:
        text = self._url_edit.text()
        return {"url": text} if text else None

    def restore_draft(self, state: dict):
        self._url_edit.setText(str(state.get("url") or ""))

    def _submit(self):
        # Apply the selected named rule immediately before enqueueing so the
        # resolver and sidecar options use the same snapshot.
        urls = split_submitted_urls(self._url_edit.text())
        if not urls:
            InfoBar.warning(
                title=tr("Notice", "提示", "お知らせ"),
                content=tr("Please input a valid URL first", "请先输入有效的 URL", "先に有効な URL を入力してください"),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )
            return
        rule_id = self._rule_picker.selected_rule_id()
        self._rule_picker.apply_selected(show_notice=False)
        mark_only = app_config.mark_submitted_as_downloaded or not app_config.download_video_file
        for url in urls:
            self._maybe_add_download_source_to_subscription(url)
            if mark_only:
                download_manager.add_url_mark_downloaded(url, rule_id=rule_id)
            else:
                download_manager.add_url(url, rule_id=rule_id)
        self._url_edit.clear()
        prefix = (
            tr("Submitted for marking: ", "已提交标记：", "マーク処理へ送信: ")
            if mark_only
            else tr("Submitted for parsing: ", "已提交解析：", "解析キューに送信: ")
        )
        if len(urls) == 1:
            url = urls[0]
            content = prefix + f"{url[:70]}{'…' if len(url) > 70 else ''}"
        else:
            content = prefix + tr(f"{len(urls)} links", f"{len(urls)} 条链接", f"{len(urls)} 件のリンク")
        InfoBar.success(
            title=tr("Submitted", "已提交", "送信しました"),
            content=content,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=3000,
            parent=self,
        )

    def _paste_from_clipboard(self, submit: bool = False):
        text = QApplication.clipboard().text().strip()
        if not text:
            return
        urls = split_submitted_urls(text)
        self._url_edit.setText(" ".join(urls) if len(urls) > 1 else text)
        self._url_edit.setFocus()
        if submit:
            self._submit()

    def _open_settings_page(self):
        window = self.window()
        settings_page = getattr(window, "_settings_page", None)
        if settings_page is not None and hasattr(window, "switchTo"):
            window.switchTo(settings_page)

    def _on_login_state(self, logged_in: bool):
        if logged_in:
            self._login_status_lbl.setText(tr("✓ Signed in", "✓ 已登录", "✓ ログイン済み"))
            set_status_text(self._login_status_lbl, "success")
            self._login_status_lbl.setToolTip("")
        else:
            self._login_status_lbl.setText(tr("Not signed in", "未登录", "未ログイン"))
            set_secondary_text(self._login_status_lbl)
            self._login_status_lbl.setToolTip(
                tr(
                    "Private videos require login",
                    "私有视频需要登录账号",
                    "非公開動画にはログインが必要です",
                )
            )
        self._login_settings_btn.setVisible(not logged_in)

    def _copy_log(self):
        text = self._log_edit.toPlainText()
        if not text:
            return
        QApplication.clipboard().setText(text)
        InfoBar.success(
            title=tr("Copied", "已复制", "コピーしました"),
            content=tr("Runtime log copied", "运行日志已复制到剪贴板", "実行ログをコピーしました"),
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=1600,
            parent=self,
        )

    def _append_log(self, msg: str):
        self._pending_logs.append(str(msg))
        if not self._log_flush_timer.isActive():
            self._log_flush_timer.start()

    def _flush_logs(self):
        if not self._pending_logs:
            self._log_flush_timer.stop()
            return
        chunk = self._pending_logs[:200]
        del self._pending_logs[:200]
        self._log_edit.appendPlainText("\n".join(chunk))
        sb = self._log_edit.verticalScrollBar()
        sb.setValue(sb.maximum())
        if not self._pending_logs:
            self._log_flush_timer.stop()

    def _clear_log(self):
        self._pending_logs.clear()
        self._log_edit.clear()

    def _emit_options_changed(self):
        signal_bus.download_options_changed.emit()

    def _on_filter_state_changed(self):
        state = tr("enabled", "启用", "有効") if app_config.filter_enabled else tr("disabled", "关闭", "無効")
        signal_bus.log_message.emit(
            tr(
                f"[Filter] {state}",
                f"[筛选] 已{state}",
                f"[フィルター] {state}",
            )
        )

    def refresh_theme_styles(self):
        """Reapply custom option-button colors after a runtime theme switch."""
        if hasattr(self, "_download_video_btn"):
            self._sync_option_controls()
        if hasattr(self, "_log_edit"):
            apply_scrollbars(self._log_edit)
        if hasattr(self, "_task_center"):
            self._task_center.refresh_theme_styles()

    def _on_download_video_clicked(self, checked: bool):
        if self._syncing_options:
            return
        app_config.download_video_file = bool(checked)
        app_config.mark_submitted_as_downloaded = not bool(checked)
        self._emit_options_changed()

    def _on_download_thumb_clicked(self, checked: bool):
        if self._syncing_options:
            return
        app_config.download_thumbnail = bool(checked)
        self._emit_options_changed()

    def _on_collect_nfo_clicked(self, checked: bool):
        if self._syncing_options:
            return
        app_config.collect_nfo_info = bool(checked)
        self._emit_options_changed()

    def _on_mark_downloaded_clicked(self, checked: bool):
        if self._syncing_options:
            return
        app_config.mark_submitted_as_downloaded = bool(checked)
        app_config.download_video_file = not bool(checked)
        self._emit_options_changed()

    def _sync_option_controls(self):
        if app_config.mark_submitted_as_downloaded and app_config.download_video_file:
            app_config.download_video_file = False
        if not app_config.mark_submitted_as_downloaded and not app_config.download_video_file:
            app_config.download_video_file = True

        self._syncing_options = True
        try:
            self._set_option_button_state(self._download_video_btn, app_config.download_video_file)
            self._set_option_button_state(self._mark_downloaded_btn, app_config.mark_submitted_as_downloaded)
            self._set_option_button_state(self._download_thumb_btn, app_config.download_thumbnail)
            self._set_option_button_state(self._collect_nfo_btn, app_config.collect_nfo_info)
        finally:
            self._syncing_options = False

    def _set_option_button_state(self, button: PrimaryPushButton, checked: bool):
        button.setChecked(checked)
        state = tr("On", "开", "オン") if checked else tr("Off", "关", "オフ")
        base_text = str(getattr(button, "_base_text", button.text()) or "")
        button.setText(f"{base_text}  {state}")
        button.setStyleSheet(option_button_style(checked))

    def _maybe_add_download_source_to_subscription(self, url: str):
        candidate = download_manager.detect_subscription_source(url)
        if not candidate:
            return
        kind, key = candidate
        mode = app_config.subscription_prompt_mode
        if mode == "never":
            return
        if mode == "always":
            self._add_subscription_source(kind, key)
            return

        box = _SubscriptionPromptDialog(self)
        accepted = box.exec() == QDialog.DialogCode.Accepted
        if accepted and box.choice == "always":
            app_config.subscription_prompt_mode = "always"
            self._add_subscription_source(kind, key)
        elif accepted:
            if box.no_remind.isChecked():
                app_config.subscription_prompt_mode = "never"
            self._add_subscription_source(kind, key)
        elif box.no_remind.isChecked():
            app_config.subscription_prompt_mode = "never"

    def _add_subscription_source(self, kind: str, key: str):
        source_id = download_manager.add_detected_subscription_source(kind, key)
        if source_id:
            signal_bus.log_message.emit(
                tr(
                    f"[Subscriptions] added local source: {kind}:{key}",
                    f"[订阅] 已加入本地订阅源：{kind}:{key}",
                    f"[購読] ローカル購読元を追加: {kind}:{key}",
                )
            )
            signal_bus.subscription_source_added.emit(source_id)

    def _open_filter_dialog(self):
        dlg = FilterDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._on_filter_state_changed()
            self._emit_options_changed()
            InfoBar.success(
                title=tr("Filter rules saved", "筛选条件已保存", "フィルター条件を保存しました"),
                content=tr(
                    "New tasks will be filtered during resolving",
                    "新提交任务将按当前筛选规则解析后决定是否下载",
                    "新規タスクは解析時に現在の条件で判定されます",
                ),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=2500,
                parent=self,
            )
