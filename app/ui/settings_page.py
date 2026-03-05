"""Settings Interface — login, quality, download dir, concurrency, proxy."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    ComboBox,
    FluentIcon,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    PasswordLineEdit,
    PrimaryPushButton,
    ScrollArea,
    Slider,
    SubtitleLabel,
    SwitchButton,
    TitleLabel,
    ToolButton,
)

from ..config import app_config
from ..core.manager import download_manager
from ..i18n import tr
from ..signal_bus import signal_bus


# ── Worker thread for login ───────────────────────────────────────────────────

class LoginWorker(QThread):
    finished = Signal(bool, str)  # success, msg

    def __init__(self, credential: str, password: str):
        super().__init__()
        self._credential = credential
        self._password = password

    def run(self):
        ok, msg = download_manager.api.login(self._credential, self._password)
        self.finished.emit(ok, msg)


# ── Settings Interface ────────────────────────────────────────────────────────

class SettingsInterface(ScrollArea):
    """Page for configuring application-level settings (including login)."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("SettingsInterface")

        self._worker: LoginWorker | None = None

        self._content = QWidget(self)
        self._content.setObjectName("settingsContent")
        self.setWidget(self._content)
        self.setWidgetResizable(True)

        self._build_ui()
        self._load_settings()

        # Auto-login on startup if credentials are saved
        if app_config.auth_enabled and app_config.username and app_config.password:
            self._do_login(silent=True)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self._content)
        layout.setContentsMargins(36, 24, 36, 24)
        layout.setSpacing(16)

        layout.addWidget(TitleLabel(tr("Settings", "应用设置"), self._content))

        # ── Language ─────────────────────────────────────────────────────────
        lang_card = CardWidget(self._content)
        lang_layout = QVBoxLayout(lang_card)
        lang_layout.setContentsMargins(20, 16, 20, 16)
        lang_layout.setSpacing(10)

        lang_layout.addWidget(SubtitleLabel("界面语言", lang_card))
        lang_layout.addWidget(BodyLabel("默认中文；切换后重启程序生效", lang_card))

        lang_row = QHBoxLayout()
        self._lang_combo = ComboBox(lang_card)
        self._lang_combo.addItems(["简体中文", "English"])
        self._lang_combo.setFixedWidth(180)
        self._lang_combo.currentIndexChanged.connect(self._on_language_changed)
        lang_row.addWidget(self._lang_combo)
        lang_row.addStretch()
        lang_layout.addLayout(lang_row)

        layout.addWidget(lang_card)

        # ── Data location (portable mode) ───────────────────────────────────
        data_card = CardWidget(self._content)
        data_layout = QVBoxLayout(data_card)
        data_layout.setContentsMargins(20, 16, 20, 16)
        data_layout.setSpacing(8)
        data_layout.addWidget(SubtitleLabel(tr("Local Data Paths (Portable)", "本地数据位置（绿色模式）"), data_card))
        data_layout.addWidget(BodyLabel(f"{tr('Data dir', '数据目录')}: {app_config.app_data_dir}", data_card))
        data_layout.addWidget(BodyLabel(f"{tr('Config file', '配置文件')}: {app_config.config_path}", data_card))
        data_layout.addWidget(BodyLabel(f"{tr('History DB', '下载历史库')}: {app_config.history_db_path}", data_card))
        layout.addWidget(data_card)

        # ── Account / Login card ──────────────────────────────────────────────
        login_card = CardWidget(self._content)
        login_layout = QVBoxLayout(login_card)
        login_layout.setContentsMargins(20, 16, 20, 16)
        login_layout.setSpacing(10)

        login_header = QHBoxLayout()
        login_header.addWidget(SubtitleLabel(tr("Account Login", "账号登录"), login_card))
        login_header.addStretch()
        self._auth_switch = SwitchButton(login_card)
        self._auth_switch.checkedChanged.connect(self._on_auth_toggle)
        login_header.addWidget(self._auth_switch)
        login_layout.addLayout(login_header)

        login_layout.addWidget(
            BodyLabel(
                "登录后可下载私有视频；账号密码在本地持久化保存，下次启动自动登录",
                login_card,
            )
        )

        self._cred_widget = QWidget(login_card)
        cred_layout = QVBoxLayout(self._cred_widget)
        cred_layout.setContentsMargins(0, 4, 0, 0)
        cred_layout.setSpacing(8)

        self._user_edit = LineEdit(self._cred_widget)
        self._user_edit.setPlaceholderText("用户名或邮箱（支持两种格式）")
        self._user_edit.setClearButtonEnabled(True)

        self._pass_edit = PasswordLineEdit(self._cred_widget)
        self._pass_edit.setPlaceholderText("密码")

        btn_row = QHBoxLayout()
        self._login_btn = PrimaryPushButton(tr("Login", "登录"), self._cred_widget, FluentIcon.PEOPLE)
        self._login_btn.setFixedWidth(100)
        self._login_btn.clicked.connect(lambda: self._do_login(silent=False))

        self._logout_btn = PrimaryPushButton(tr("Logout", "退出登录"), self._cred_widget, FluentIcon.CANCEL)
        self._logout_btn.setFixedWidth(110)
        self._logout_btn.clicked.connect(self._do_logout)
        self._logout_btn.hide()

        btn_row.addWidget(self._login_btn)
        btn_row.addWidget(self._logout_btn)
        btn_row.addStretch()

        self._login_status_lbl = BodyLabel("", self._cred_widget)

        cred_layout.addWidget(self._user_edit)
        cred_layout.addWidget(self._pass_edit)
        cred_layout.addLayout(btn_row)
        cred_layout.addWidget(self._login_status_lbl)

        login_layout.addWidget(self._cred_widget)
        layout.addWidget(login_card)

        # ── Quality preference card ───────────────────────────────────────────
        quality_card = CardWidget(self._content)
        quality_layout = QVBoxLayout(quality_card)
        quality_layout.setContentsMargins(20, 16, 20, 16)
        quality_layout.setSpacing(10)

        quality_layout.addWidget(SubtitleLabel(tr("Preferred Quality", "下载画质偏好"), quality_card))
        quality_layout.addWidget(
            BodyLabel(
                "首选画质不存在时自动回退到更低分辨率：Source > 540 > 360",
                quality_card,
            )
        )

        quality_row = QHBoxLayout()
        self._quality_combo = ComboBox(quality_card)
        self._quality_combo.addItems(["Source（原画）", "540p", "360p"])
        self._quality_combo.setFixedWidth(180)
        self._quality_combo.currentIndexChanged.connect(self._on_quality_changed)
        quality_row.addWidget(self._quality_combo)
        quality_row.addStretch()
        quality_layout.addLayout(quality_row)
        layout.addWidget(quality_card)

        # ── Download directory ────────────────────────────────────────────────
        dir_card = CardWidget(self._content)
        dir_layout = QVBoxLayout(dir_card)
        dir_layout.setContentsMargins(20, 16, 20, 16)
        dir_layout.setSpacing(10)

        dir_layout.addWidget(SubtitleLabel(tr("Download Directory", "下载目录"), dir_card))
        dir_layout.addWidget(BodyLabel("视频文件保存位置（自动按作者名建立子文件夹）", dir_card))

        dir_row = QHBoxLayout()
        self._dir_edit = LineEdit(dir_card)
        self._dir_edit.setPlaceholderText("选择下载目录…")
        self._dir_edit.setReadOnly(True)

        browse_btn = ToolButton(FluentIcon.FOLDER, dir_card)
        browse_btn.setToolTip("浏览…")
        browse_btn.clicked.connect(self._browse_dir)

        dir_row.addWidget(self._dir_edit, stretch=1)
        dir_row.addWidget(browse_btn)
        dir_layout.addLayout(dir_row)
        layout.addWidget(dir_card)

        # ── Filename template & de-dup ───────────────────────────────────────
        name_card = CardWidget(self._content)
        name_layout = QVBoxLayout(name_card)
        name_layout.setContentsMargins(20, 16, 20, 16)
        name_layout.setSpacing(10)

        name_layout.addWidget(SubtitleLabel(tr("Filename Template", "下载命名规则"), name_card))
        name_layout.addWidget(
            BodyLabel(
                "可用占位符：{YYYY-MM-DD} {title} {id}；默认 {YYYY-MM-DD}+{title}+{id}.mp4",
                name_card,
            )
        )

        self._name_tpl_edit = LineEdit(name_card)
        self._name_tpl_edit.setPlaceholderText("{YYYY-MM-DD}+{title}+{id}.mp4")
        self._name_tpl_edit.setClearButtonEnabled(True)
        name_layout.addWidget(self._name_tpl_edit)

        skip_row = QHBoxLayout()
        skip_row.addWidget(BodyLabel("批量下载时跳过下载目录中已存在的文件（按视频ID匹配）", name_card))
        skip_row.addStretch()
        self._skip_existing_switch = SwitchButton(name_card)
        skip_row.addWidget(self._skip_existing_switch)
        name_layout.addLayout(skip_row)

        layout.addWidget(name_card)

        # ── Concurrency ───────────────────────────────────────────────────────
        conc_card = CardWidget(self._content)
        conc_layout = QVBoxLayout(conc_card)
        conc_layout.setContentsMargins(20, 16, 20, 16)
        conc_layout.setSpacing(10)

        conc_layout.addWidget(SubtitleLabel(tr("Concurrent Downloads", "并发下载数"), conc_card))
        conc_layout.addWidget(BodyLabel("同时处于解析/等待/下载状态的最大任务数（1–10）", conc_card))

        conc_row = QHBoxLayout()
        self._conc_slider = Slider(Qt.Orientation.Horizontal, conc_card)
        self._conc_slider.setRange(1, 10)
        self._conc_slider.setFixedWidth(320)
        self._conc_slider.valueChanged.connect(self._on_concurrency_changed)

        self._conc_value_lbl = BodyLabel("1", conc_card)
        self._conc_value_lbl.setFixedWidth(36)
        self._conc_value_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        conc_row.addWidget(self._conc_slider)
        conc_row.addSpacing(12)
        conc_row.addWidget(self._conc_value_lbl)
        conc_row.addStretch()
        conc_layout.addLayout(conc_row)
        layout.addWidget(conc_card)

        # ── Proxy ─────────────────────────────────────────────────────────────
        proxy_card = CardWidget(self._content)
        proxy_layout = QVBoxLayout(proxy_card)
        proxy_layout.setContentsMargins(20, 16, 20, 16)
        proxy_layout.setSpacing(10)

        proxy_header = QHBoxLayout()
        proxy_header.addWidget(SubtitleLabel(tr("Proxy", "代理设置"), proxy_card))
        proxy_header.addStretch()
        self._proxy_switch = SwitchButton(proxy_card)
        self._proxy_switch.checkedChanged.connect(self._on_proxy_toggle)
        proxy_header.addWidget(self._proxy_switch)
        proxy_layout.addLayout(proxy_header)

        proxy_layout.addWidget(BodyLabel("HTTP/SOCKS 代理（例如 http://127.0.0.1:7890）", proxy_card))

        self._proxy_widget = QWidget(proxy_card)
        proxy_inner = QHBoxLayout(self._proxy_widget)
        proxy_inner.setContentsMargins(0, 0, 0, 0)
        self._proxy_edit = LineEdit(self._proxy_widget)
        self._proxy_edit.setPlaceholderText("http://127.0.0.1:7890")
        self._proxy_edit.textChanged.connect(self._on_proxy_url_changed)

        apply_proxy_btn = PrimaryPushButton(tr("Apply", "应用"), self._proxy_widget)
        apply_proxy_btn.setFixedWidth(80)
        apply_proxy_btn.clicked.connect(self._apply_proxy)

        proxy_inner.addWidget(self._proxy_edit, stretch=1)
        proxy_inner.addWidget(apply_proxy_btn)
        proxy_layout.addWidget(self._proxy_widget)
        layout.addWidget(proxy_card)

        # ── Save button ───────────────────────────────────────────────────────
        save_btn = PrimaryPushButton(tr("Save All Settings", "保存所有设置"), self._content, FluentIcon.SAVE)
        save_btn.setFixedWidth(140)
        save_btn.clicked.connect(self._save_settings)
        layout.addWidget(save_btn, alignment=Qt.AlignmentFlag.AlignLeft)

        layout.addStretch()

    # ── Load / save ───────────────────────────────────────────────────────────

    def _load_settings(self):
        self._dir_edit.setText(app_config.download_dir)
        self._conc_slider.setValue(app_config.max_concurrent)
        self._conc_value_lbl.setText(str(app_config.max_concurrent))
        self._proxy_switch.setChecked(app_config.proxy_enabled)
        self._proxy_edit.setText(app_config.proxy_url)
        self._proxy_widget.setVisible(app_config.proxy_enabled)
        self._name_tpl_edit.setText(app_config.filename_template)
        self._skip_existing_switch.setChecked(app_config.skip_existing_files)
        self._lang_combo.setCurrentIndex(1 if app_config.ui_language.lower().startswith("en") else 0)

        # Auth
        self._auth_switch.setChecked(app_config.auth_enabled)
        self._cred_widget.setVisible(app_config.auth_enabled)
        if app_config.username:
            self._user_edit.setText(app_config.username)
        if app_config.password:
            self._pass_edit.setText(app_config.password)

        # Quality
        _q_map = {"Source": 0, "540": 1, "360": 2}
        idx = _q_map.get(app_config.preferred_quality, 0)
        self._quality_combo.setCurrentIndex(idx)

    # ── Login helpers ─────────────────────────────────────────────────────────

    def _do_login(self, silent: bool = False):
        credential = self._user_edit.text().strip()
        password = self._pass_edit.text()
        if not credential or not password:
            if not silent:
                InfoBar.warning(
                    title="提示",
                    content="请填写用户名/邮箱和密码",
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=3000,
                    parent=self,
                )
            return

        self._login_btn.setEnabled(False)
        self._login_status_lbl.setText("登录中…")
        signal_bus.log_message.emit(f"[登录] 正在登录账号 {credential}…")

        self._worker = LoginWorker(credential, password)
        self._worker.finished.connect(lambda ok, msg: self._on_login_finished(ok, msg, silent))
        self._worker.start()

    def _on_login_finished(self, ok: bool, msg: str, silent: bool):
        self._login_btn.setEnabled(True)
        if ok:
            self._login_status_lbl.setText("✓ 已登录")
            self._logout_btn.show()
            self._login_btn.hide()
            # Persist credentials
            app_config.username = self._user_edit.text().strip()
            app_config.password = self._pass_edit.text()
            app_config.auth_enabled = True
            self._auth_switch.setChecked(True)
            signal_bus.login_state_changed.emit(True)
            signal_bus.log_message.emit(f"[登录] 成功！已获取 Token（用户: {app_config.username}）")
            if not silent:
                InfoBar.success(
                    title="登录成功",
                    content=f"已登录账号：{app_config.username}",
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=3000,
                    parent=self,
                )
        else:
            self._login_status_lbl.setText(f"✗ 登录失败: {msg}")
            signal_bus.log_message.emit(f"[登录失败] {msg}")
            if not silent:
                InfoBar.error(
                    title="登录失败",
                    content=msg or "请检查账号和密码",
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=5000,
                    parent=self,
                )

    def _do_logout(self):
        download_manager.api.logout()
        self._login_status_lbl.setText("")
        self._logout_btn.hide()
        self._login_btn.show()
        signal_bus.login_state_changed.emit(False)
        signal_bus.log_message.emit("[退出登录] Token 已清除")
        InfoBar.info(
            title="已退出登录",
            content="",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=2000,
            parent=self,
        )

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_auth_toggle(self, checked: bool):
        app_config.auth_enabled = checked
        self._cred_widget.setVisible(checked)

    def _on_quality_changed(self, idx: int):
        q = ["Source", "540", "360"][idx]
        app_config.preferred_quality = q

    def _on_language_changed(self, idx: int):
        app_config.ui_language = "en_US" if idx == 1 else "zh_CN"

    def _browse_dir(self):
        current = self._dir_edit.text() or os.path.expanduser("~")
        chosen = QFileDialog.getExistingDirectory(self, "选择下载目录", current)
        if chosen:
            self._dir_edit.setText(chosen)
            app_config.download_dir = chosen

    def _on_concurrency_changed(self, value: int):
        self._conc_value_lbl.setText(str(value))
        app_config.max_concurrent = value

    def _on_proxy_toggle(self, checked: bool):
        app_config.proxy_enabled = checked
        self._proxy_widget.setVisible(checked)
        if not checked:
            download_manager.api.set_proxy("")

    def _on_proxy_url_changed(self, text: str):
        app_config.proxy_url = text

    def _apply_proxy(self):
        if app_config.proxy_enabled:
            download_manager.apply_config()
            InfoBar.success(
                title="代理已应用",
                content=f"当前代理: {app_config.proxy_url}",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )

    def _save_settings(self):
        app_config.download_dir = self._dir_edit.text()
        app_config.max_concurrent = self._conc_slider.value()
        app_config.proxy_enabled = self._proxy_switch.isChecked()
        app_config.proxy_url = self._proxy_edit.text()
        app_config.filename_template = self._name_tpl_edit.text().strip() or "{YYYY-MM-DD}+{title}+{id}.mp4"
        app_config.skip_existing_files = self._skip_existing_switch.isChecked()
        if app_config.proxy_enabled:
            download_manager.apply_config()
        InfoBar.success(
            title="设置已保存",
            content="",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=2500,
            parent=self,
        )
