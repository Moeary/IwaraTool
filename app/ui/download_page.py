"""Download Interface — URL input + operation log."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout, QWidget

from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    FluentIcon,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    PrimaryPushButton,
    ScrollArea,
    SubtitleLabel,
    TextEdit,
    TitleLabel,
)

from ..core.manager import download_manager
from ..i18n import tr
from ..signal_bus import signal_bus


# ── Download Interface ────────────────────────────────────────────────────────

class DownloadInterface(ScrollArea):
    """Page for submitting new download URLs."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("DownloadInterface")

        self._content = QWidget(self)
        self._content.setObjectName("scrollContent")
        self.setWidget(self._content)
        self.setWidgetResizable(True)

        self._build_ui()
        signal_bus.log_message.connect(self._append_log)
        signal_bus.login_state_changed.connect(self._on_login_state)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self._content)
        layout.setContentsMargins(36, 24, 36, 24)
        layout.setSpacing(16)

        layout.addWidget(TitleLabel(tr("New Download", "新建下载"), self._content))

        # ── Login status banner ───────────────────────────────────────────────
        self._login_banner = CardWidget(self._content)
        banner_layout = QHBoxLayout(self._login_banner)
        banner_layout.setContentsMargins(20, 10, 20, 10)
        self._login_status_lbl = BodyLabel(
            tr(
                "Not logged in — go to Settings to sign in (private videos require login)",
                "未登录  —  前往「应用设置」页面可登录账号（私有视频需要登录）",
            ),
            self._login_banner,
        )
        banner_layout.addWidget(self._login_status_lbl)
        banner_layout.addStretch()
        layout.addWidget(self._login_banner)

        # ── URL input card ────────────────────────────────────────────────────
        url_card = CardWidget(self._content)
        url_layout = QVBoxLayout(url_card)
        url_layout.setContentsMargins(20, 16, 20, 16)
        url_layout.setSpacing(10)

        url_layout.addWidget(SubtitleLabel(tr("Target URL", "目标地址"), url_card))
        url_layout.addWidget(
            BodyLabel(
                tr(
                    "Supports single video, profile page, and playlist URLs; submit one URL each time",
                    "支持单视频链接、作者主页链接、播放列表链接；每次提交一个地址",
                ),
                url_card,
            )
        )

        url_row = QHBoxLayout()
        self._url_edit = LineEdit(url_card)
        self._url_edit.setPlaceholderText(
            tr(
                "https://www.iwara.tv/video/...  or profile / playlist URL",
                "https://www.iwara.tv/video/...  或  用户主页  /  播放列表链接",
            )
        )
        self._url_edit.setClearButtonEnabled(True)
        self._url_edit.returnPressed.connect(self._submit)

        self._submit_btn = PrimaryPushButton(tr("Parse & Download", "解析并下载"), url_card, FluentIcon.DOWNLOAD)
        self._submit_btn.clicked.connect(self._submit)
        self._submit_btn.setFixedWidth(130)

        url_row.addWidget(self._url_edit, stretch=1)
        url_row.addWidget(self._submit_btn)
        url_layout.addLayout(url_row)
        layout.addWidget(url_card)

        # ── Operation log card ────────────────────────────────────────────────
        log_card = CardWidget(self._content)
        log_layout = QVBoxLayout(log_card)
        log_layout.setContentsMargins(20, 16, 20, 16)
        log_layout.setSpacing(8)

        log_header = QHBoxLayout()
        log_header.addWidget(SubtitleLabel(tr("Runtime Log", "运行日志"), log_card))
        log_header.addStretch()
        clear_log_btn = PrimaryPushButton(tr("Clear", "清空"), log_card, FluentIcon.DELETE)
        clear_log_btn.setFixedWidth(80)
        clear_log_btn.clicked.connect(self._clear_log)
        log_header.addWidget(clear_log_btn)
        log_layout.addLayout(log_header)

        self._log_edit = TextEdit(log_card)
        self._log_edit.setReadOnly(True)
        from PySide6.QtGui import QFont
        mono = QFont("Consolas", 9)
        if not mono.exactMatch():
            mono = QFont("Courier New", 9)
        self._log_edit.setFont(mono)
        self._log_edit.setMinimumHeight(300)
        self._log_edit.setPlaceholderText(tr("Logs will appear here…", "操作日志将显示在此…"))
        log_layout.addWidget(self._log_edit)
        layout.addWidget(log_card)

        layout.addStretch()

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _submit(self):
        url = self._url_edit.text().strip()
        if not url:
            InfoBar.warning(
                title=tr("Notice", "提示"),
                content=tr("Please input a valid URL first", "请先输入有效的 URL"),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )
            return
        download_manager.add_url(url)
        self._url_edit.clear()
        InfoBar.success(
            title=tr("Added to Queue", "已加入队列"),
            content=tr("Submitted for parsing: ", "已提交解析：") + f"{url[:70]}{'…' if len(url) > 70 else ''}",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=3000,
            parent=self,
        )

    def _on_login_state(self, logged_in: bool):
        if logged_in:
            from ..config import app_config
            user = app_config.username
            self._login_status_lbl.setText(tr("✓ Signed in as: ", "✓ 已登录账号：") + user)
        else:
            self._login_status_lbl.setText(
                tr(
                    "Not logged in — go to Settings to sign in (private videos require login)",
                    "未登录  —  前往「应用设置」页面可登录账号（私有视频需要登录）",
                )
            )

    def _append_log(self, msg: str):
        self._log_edit.append(msg)
        sb = self._log_edit.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _clear_log(self):
        self._log_edit.clear()
