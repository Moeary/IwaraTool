"""Download Interface — URL input + operation log."""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QSizePolicy,
    QSplitter,
    QSplitterHandle,
    QVBoxLayout,
    QWidget,
)

from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    CheckBox,
    FluentIcon,
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
    isDarkTheme,
)

from ..config import app_config
from ..core.manager import download_manager
from ..i18n import tr
from ..signal_bus import signal_bus
from .rules_page import RulePicker
from .task_page import TaskCenterInterface
from .ui_state import ResponsiveFlowLayout


def fluent_scrollbar_style() -> str:
    handle = "#6f7d89" if isDarkTheme() else "#9aa7b2"
    hover = "#22c3cf" if isDarkTheme() else "#00a4af"
    return f"""
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px 1px 2px 1px; }}
    QScrollBar::handle:vertical {{ background: {handle}; min-height: 38px; border-radius: 5px; }}
    QScrollBar::handle:vertical:hover {{ background: {hover}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 1px 2px 1px 2px; }}
    QScrollBar::handle:horizontal {{ background: {handle}; min-width: 38px; border-radius: 5px; }}
    QScrollBar::handle:horizontal:hover {{ background: {hover}; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0px; }}
    """


class _FluentWorkbenchSplitterHandle(QSplitterHandle):
    """Theme-neutral splitter handle used by the workbench's two panes."""

    def __init__(self, orientation: Qt.Orientation, parent: QSplitter):
        super().__init__(orientation, parent)
        self._grip = QFrame(self)
        self._grip.setObjectName("WorkbenchSplitterGrip")
        self._grip.setFrameShape(QFrame.Shape.NoFrame)
        self._grip.setStyleSheet(
            "QFrame#WorkbenchSplitterGrip { background: rgba(0, 160, 170, 0.48); border-radius: 3px; }"
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.orientation() == Qt.Orientation.Vertical:
            grip_width, grip_height = min(56, max(32, self.width() - 12)), 4
        else:
            grip_width, grip_height = 4, min(56, max(32, self.height() - 12))
        self._grip.setGeometry(
            max(0, (self.width() - grip_width) // 2),
            max(0, (self.height() - grip_height) // 2),
            grip_width,
            grip_height,
        )


class _FluentWorkbenchSplitter(QSplitter):
    def createHandle(self):
        return _FluentWorkbenchSplitterHandle(self.orientation(), self)


def _style_workbench_splitter(splitter: QSplitter):
    if isDarkTheme():
        base = "rgba(255, 255, 255, 0.06)"
        hover = "rgba(255, 255, 255, 0.18)"
        pressed = "rgba(255, 255, 255, 0.28)"
    else:
        base = "rgba(0, 0, 0, 0.04)"
        hover = "rgba(0, 0, 0, 0.10)"
        pressed = "rgba(0, 0, 0, 0.18)"
    splitter.setStyleSheet(
        f"""
        QSplitter::handle {{ background: {base}; }}
        QSplitter::handle:horizontal {{ height: 10px; }}
        QSplitter::handle:vertical {{ width: 10px; }}
        QSplitter::handle:hover {{ background: {hover}; }}
        QSplitter::handle:pressed {{ background: {pressed}; }}
        """
    )


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


_OPTION_ON_STYLE = """
QPushButton {
    background-color: #009faa;
    color: white;
    border: 1px solid #008c96;
    border-radius: 6px;
    padding: 8px 10px;
}
QPushButton:hover { background-color: #00aeba; }
QPushButton:pressed { background-color: #008c96; }
"""

_OPTION_OFF_STYLE = """
QPushButton {
    background-color: #f5f7fa;
    color: #32363a;
    border: 1px solid #d0d7de;
    border-radius: 6px;
    padding: 8px 10px;
}
QPushButton:hover { background-color: #edf1f5; }
QPushButton:pressed { background-color: #e3e8ef; }
"""

_OPTION_ON_DARK_STYLE = """
QPushButton {
    background-color: #087f89;
    color: #f7ffff;
    border: 1px solid #18a8b2;
    border-radius: 6px;
    padding: 8px 10px;
}
QPushButton:hover { background-color: #1099a4; }
QPushButton:pressed { background-color: #066a73; }
"""

_OPTION_OFF_DARK_STYLE = """
QPushButton {
    background-color: #292d32;
    color: #eef2f5;
    border: 1px solid #4c555e;
    border-radius: 6px;
    padding: 8px 10px;
}
QPushButton:hover { background-color: #343a41; }
QPushButton:pressed { background-color: #202429; }
"""


def option_button_style(checked: bool) -> str:
    """Return a theme-aware style for the download option toggles."""
    if isDarkTheme():
        return _OPTION_ON_DARK_STYLE if checked else _OPTION_OFF_DARK_STYLE
    return _OPTION_ON_STYLE if checked else _OPTION_OFF_STYLE


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
        root.setContentsMargins(28, 22, 28, 18)
        root.setSpacing(12)

        title_row = ResponsiveFlowLayout()
        title_row.addWidget(TitleLabel(tr("Download Workbench", "下载工作台", "ダウンロードワークベンチ"), self))
        root.addLayout(title_row)

        splitter = _FluentWorkbenchSplitter(Qt.Orientation.Horizontal, self)
        self._splitter = splitter
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(10)
        _style_workbench_splitter(splitter)
        root.addWidget(splitter, stretch=1)

        left_panel = QWidget(self)
        left_panel.setMinimumWidth(320)
        left_panel.setMaximumWidth(520)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(12)
        splitter.addWidget(left_panel)

        right_panel = QWidget(self)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([390, 1180])

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

        # ── Login status banner ───────────────────────────────────────────────
        self._login_banner = CardWidget(left_panel)
        banner_layout = QHBoxLayout(self._login_banner)
        banner_layout.setContentsMargins(14, 8, 14, 8)
        self._login_status_lbl = BodyLabel(
            tr(
                "Not logged in — go to Settings to sign in (private videos require login)",
                "未登录  —  前往「应用设置」页面可登录账号（私有视频需要登录）",
                "未ログイン — 設定画面でログインしてください（非公開動画はログイン必須）",
            ),
            self._login_banner,
        )
        banner_layout.addWidget(self._login_status_lbl)
        banner_layout.addStretch()
        left_layout.addWidget(self._login_banner)

        # ── URL input card ────────────────────────────────────────────────────
        url_card = CardWidget(left_panel)
        url_layout = QVBoxLayout(url_card)
        url_layout.setContentsMargins(16, 14, 16, 14)
        url_layout.setSpacing(10)

        url_layout.addWidget(SubtitleLabel(tr("Target URL", "目标地址", "対象URL"), url_card))
        url_layout.addWidget(
            BodyLabel(
                tr(
                    "Supports video/profile/playlist URLs and API search URLs",
                    "支持单视频、作者主页、播放列表和 API 搜索链接",
                    "動画/プロフィール/プレイリスト/API検索URLに対応",
                ),
                url_card,
            )
        )

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
        self._submit_btn = PrimaryPushButton(
            tr("Download", "下载", "ダウンロード"),
            url_card,
            FluentIcon.DOWNLOAD,
        )
        self._submit_btn.setMinimumWidth(112)
        self._submit_btn.clicked.connect(self._submit)
        url_row.addWidget(self._submit_btn)
        url_layout.addLayout(url_row)

        rule_row = QHBoxLayout()
        rule_row.setSpacing(8)
        rule_row.addWidget(BodyLabel(tr("Rule", "下载规则", "ルール"), url_card))
        self._rule_picker = RulePicker(url_card)
        rule_row.addWidget(self._rule_picker, 1)
        url_layout.addLayout(rule_row)
        left_layout.addWidget(url_card)

        # ── Operation log card ────────────────────────────────────────────────
        log_card = CardWidget(left_panel)
        log_card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        log_layout = QVBoxLayout(log_card)
        log_layout.setContentsMargins(16, 14, 16, 14)
        log_layout.setSpacing(8)

        log_header = ResponsiveFlowLayout()
        log_header.addWidget(SubtitleLabel(tr("Runtime Log", "运行日志", "実行ログ"), log_card))
        clear_log_btn = PrimaryPushButton(tr("Clear", "清空", "クリア"), log_card, FluentIcon.DELETE)
        clear_log_btn.setFixedWidth(80)
        clear_log_btn.clicked.connect(self._clear_log)
        log_header.addWidget(clear_log_btn)
        log_layout.addLayout(log_header)

        self._log_edit = PlainTextEdit(log_card)
        self._log_edit.setReadOnly(True)
        self._log_edit.setMaximumBlockCount(self._MAX_LOG_BLOCKS)
        self._log_edit.verticalScrollBar().setStyleSheet(fluent_scrollbar_style())
        self._log_edit.horizontalScrollBar().setStyleSheet(fluent_scrollbar_style())
        from PySide6.QtGui import QFont
        mono = QFont("Consolas", 9)
        if not mono.exactMatch():
            mono = QFont("Courier New", 9)
        self._log_edit.setFont(mono)
        self._log_edit.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self._log_edit.setMinimumHeight(180)
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

    def _submit(self):
        # Apply the selected named rule immediately before enqueueing so the
        # resolver and sidecar options use the same snapshot.
        url = self._url_edit.text().strip()
        if not url:
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
        self._maybe_add_download_source_to_subscription(url)
        rule_id = self._rule_picker.selected_rule_id()
        self._rule_picker.apply_selected(show_notice=False)
        mark_only = app_config.mark_submitted_as_downloaded or not app_config.download_video_file
        if mark_only:
            download_manager.add_url_mark_downloaded(url, rule_id=rule_id)
        else:
            download_manager.add_url(url, rule_id=rule_id)
        self._url_edit.clear()
        InfoBar.success(
            title=tr("Submitted", "已提交", "送信しました"),
            content=(
                tr("Submitted for marking: ", "已提交标记：", "マーク処理へ送信: ")
                if mark_only
                else tr("Submitted for parsing: ", "已提交解析：", "解析キューに送信: ")
            )
            + f"{url[:70]}{'…' if len(url) > 70 else ''}",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=3000,
            parent=self,
        )

    def _on_login_state(self, logged_in: bool):
        if logged_in:
            self._login_status_lbl.setText(
                tr("✓ Signed in", "✓ 已登录", "✓ ログイン済み")
            )
        else:
            self._login_status_lbl.setText(
                tr(
                    "Not logged in — go to Settings to sign in (private videos require login)",
                    "未登录  —  前往「应用设置」页面可登录账号（私有视频需要登录）",
                    "未ログイン — 設定画面でログインしてください（非公開動画はログイン必須）",
                )
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
        if hasattr(self, "_splitter"):
            _style_workbench_splitter(self._splitter)
        if hasattr(self, "_download_video_btn"):
            self._sync_option_controls()
        if hasattr(self, "_log_edit"):
            self._log_edit.verticalScrollBar().setStyleSheet(fluent_scrollbar_style())
            self._log_edit.horizontalScrollBar().setStyleSheet(fluent_scrollbar_style())

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
