"""Download Interface — URL input + operation log."""
from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    FluentIcon,
    HyperlinkButton,
    PushButton,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    PlainTextEdit,
    PrimaryPushButton,
    SubtitleLabel,
    TitleLabel,
    ToolButton,
    TransparentToolButton,
)

from ...config import app_config
from ...core.manager import download_manager
from ...i18n import tr
from ...signal_bus import signal_bus
from ..rules.rule_picker import RulePicker
from .task_center import TaskCenterInterface
from ..theme import (
    CARD_MARGINS,
    PAGE_MARGINS,
    PAGE_SPACING,
    FluentSplitter,
    apply_scrollbars,
    set_secondary_text,
    set_status_text,
)
from .download_options import DownloadOptionsMixin
from .link_tools import DownloadLinkToolsMixin
from .staging_panel import StagingPanel
from .log_panel import DownloadLogMixin
from .submitted_urls import split_submitted_urls
from .subscription_prompt import DownloadSubscriptionPromptMixin


# ── Download Interface ────────────────────────────────────────────────────────

class DownloadInterface(
    DownloadLinkToolsMixin,
    DownloadSubscriptionPromptMixin,
    DownloadOptionsMixin,
    DownloadLogMixin,
    QWidget,
):
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
        self._init_link_tools()

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

        root.addLayout(self._build_title_row())

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

        self._build_legacy_option_buttons(left_panel)
        left_layout.addWidget(self._build_url_card(left_panel))
        left_layout.addWidget(self._build_log_card(left_panel), stretch=1)

        right_layout.setSpacing(8)
        self._staging_panel = StagingPanel(right_panel)  # hidden while nothing waits
        right_layout.addWidget(self._staging_panel)
        self._task_center = TaskCenterInterface(right_panel, embedded=True)
        self._task_center.explain_requested.connect(self.explain_video)
        self._staging_panel.show_requested.connect(self._task_center.show_staged)
        right_layout.addWidget(self._task_center, stretch=1)

    def _build_title_row(self) -> QHBoxLayout:
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
        return title_row

    def _build_legacy_option_buttons(self, left_panel: QWidget):
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

    def _build_url_card(self, left_panel: QWidget) -> CardWidget:
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
        submit_row = QHBoxLayout()
        submit_row.setSpacing(8)
        submit_row.addWidget(self._submit_btn, 1)
        self._why_btn = PushButton(tr("Why not downloaded?", "为什么没下载？", "なぜ保存されない？"), url_card, FluentIcon.HELP)
        self._why_btn.setToolTip(
            tr(
                "Check the video link in the box: queue, history, files on disk, the site and the rule",
                "检查输入框中的视频：任务队列、历史记录、本地文件、站点状态与下载规则",
                "入力欄の動画を確認します：キュー・履歴・ローカルファイル・サイト・ルール",
            )
        )
        self._why_btn.clicked.connect(self._explain_current_url)
        submit_row.addWidget(self._why_btn)
        url_layout.addLayout(submit_row)
        return url_card

    def _build_log_card(self, left_panel: QWidget) -> CardWidget:
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
        return log_card

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
        if len(urls) == 1 and download_manager.listing_kind(urls[0]):
            # An author / playlist: show what it holds before queueing anything.
            self._start_listing_preview(urls[0], rule_id)
            return
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

    def refresh_theme_styles(self):
        """Reapply custom option-button colors after a runtime theme switch."""
        if hasattr(self, "_download_video_btn"):
            self._sync_option_controls()
        if hasattr(self, "_log_edit"):
            apply_scrollbars(self._log_edit)
        if hasattr(self, "_task_center"):
            self._task_center.refresh_theme_styles()
