"""Account card: Iwara login / logout off the GUI thread."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import (
    BodyLabel,
    FluentIcon,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    PasswordLineEdit,
    PrimaryPushButton,
    PushButton,
    SubtitleLabel,
    SwitchButton,
)

from ...config import app_config
from ...i18n import tr
from ...signal_bus import signal_bus
from ..worker_lifecycle import ManagedThread
from .page_dependencies import download_manager


class LoginWorker(ManagedThread):
    # Not ``finished``: that is QThread's own signal, which the page waits for
    # before letting the thread go.
    result_ready = Signal(bool, str)  # success, msg

    def __init__(self, credential: str, password: str):
        super().__init__()
        self._credential = credential
        self._password = password

    def run(self):
        ok, msg = download_manager.api.login(self._credential, self._password)
        self.result_ready.emit(ok, msg)


class SettingsAccountMixin:
    """Account card: Iwara login / logout off the GUI thread."""

    def _build_account_card(self):
        login_card = self._settings_board.create_card("account")
        login_layout = QVBoxLayout(login_card)
        login_layout.setContentsMargins(20, 16, 20, 16)
        login_layout.setSpacing(10)

        login_header = QHBoxLayout()
        login_header.addWidget(SubtitleLabel(tr("Account Login", "账号登录", "アカウントログイン"), login_card))
        login_header.addStretch()
        self._auth_switch = SwitchButton(login_card)
        self._auth_switch.checkedChanged.connect(self._on_auth_toggle)
        login_header.addWidget(self._auth_switch)
        login_layout.addLayout(login_header)

        login_layout.addWidget(
            BodyLabel(
                tr(
                    "Private videos require login.",
                    "私有视频需要登录账号。",
                    "非公開動画にはログインが必要です。",
                ),
                login_card,
            )
        )

        self._cred_widget = QWidget(login_card)
        cred_layout = QVBoxLayout(self._cred_widget)
        cred_layout.setContentsMargins(0, 4, 0, 0)
        cred_layout.setSpacing(8)

        self._user_edit = LineEdit(self._cred_widget)
        self._user_edit.setPlaceholderText(tr("Username", "用户名", "ユーザー名"))
        self._user_edit.setClearButtonEnabled(True)

        self._pass_edit = PasswordLineEdit(self._cred_widget)
        self._pass_edit.setPlaceholderText(tr("Password", "密码", "パスワード"))

        btn_row = QHBoxLayout()
        self._login_btn = PrimaryPushButton(tr("Login", "登录", "ログイン"), self._cred_widget, FluentIcon.PEOPLE)
        self._login_btn.setFixedWidth(100)
        self._login_btn.clicked.connect(lambda: self._do_login(silent=False))

        self._logout_btn = PushButton(tr("Logout", "退出登录", "ログアウト"), self._cred_widget, FluentIcon.CANCEL)
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
        self._settings_board.add_card("account", login_card)

    def _set_logged_in_ui(self, logged_in: bool, status_text: str = ""):
        self._login_ui_state = (logged_in, status_text)
        if not self._built:
            return  # applied when the page is built
        if logged_in:
            self._login_status_lbl.setText(status_text or tr("✓ Signed in", "✓ 已登录", "✓ ログイン済み"))
            self._logout_btn.show()
            self._login_btn.hide()
            return
        self._login_status_lbl.setText(status_text)
        self._logout_btn.hide()
        self._login_btn.show()

    def _do_login(self, silent: bool = False):
        credential = self._user_edit.text().strip()
        password = self._pass_edit.text()
        if not credential or not password:
            if not silent:
                InfoBar.warning(
                    title=tr("Notice", "提示", "お知らせ"),
                    content=tr("Please enter username/email and password", "请填写用户名/邮箱和密码", "ユーザー名/メールとパスワードを入力してください"),
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=3000,
                    parent=self,
                )
            return

        if not silent and not app_config.api_proxy_enabled:
            InfoBar.warning(
                title=tr("API proxy is off", "API 代理未开启", "API プロキシ無効"),
                content=tr(
                    "If TUN/system proxy is not enabled, login and URL parsing may fail.",
                    "如果没有开启 TUN/系统代理，登录和链接解析可能会失败。",
                    "TUN/システムプロキシが無効な場合、ログインや URL 解析に失敗する可能性があります。",
                ),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=5000,
                parent=self,
            )

        if self._worker is not None and self._worker.isRunning():
            return  # a sign-in is already on its way
        self._login_btn.setEnabled(False)
        self._login_status_lbl.setText(tr("Signing in...", "登录中…", "ログイン中..."))
        signal_bus.log_message.emit(
            tr(
                "[Login] Signing in ...",
                "[登录] 正在登录…",
                "[ログイン] サインイン中...",
            )
        )

        download_manager.apply_config()
        worker = LoginWorker(credential, password)
        worker.result_ready.connect(lambda ok, msg: self._on_login_finished(ok, msg, silent))
        worker.finished.connect(lambda worker=worker: self._release_login_worker(worker))
        self._worker = worker
        worker.start()

    def _release_login_worker(self, worker: LoginWorker):
        if self._worker is worker:
            self._worker = None
        worker.deleteLater()

    def _on_login_finished(self, ok: bool, msg: str, silent: bool):
        self._login_btn.setEnabled(True)
        if ok:
            self._set_logged_in_ui(True)
            # Persist credentials
            app_config.username = self._user_edit.text().strip()
            app_config.password = self._pass_edit.text()
            app_config.auth_enabled = True
            self._auth_switch.setChecked(True)
            download_manager.set_login(True, download_manager.api.token)
            signal_bus.login_state_changed.emit(True)
            signal_bus.log_message.emit(
                tr(
                    "[Login] Success. Token cached",
                    "[登录] 成功！已获取并缓存 Token",
                    "[ログイン] 成功。Token を保存しました",
                )
            )
            if not silent:
                InfoBar.success(
                    title=tr("Login successful", "登录成功", "ログイン成功"),
                    content=tr("Signed in", "已登录", "ログイン済み"),
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=3000,
                    parent=self,
                )
        else:
            self._set_logged_in_ui(False, tr(f"✗ Login failed: {msg}", f"✗ 登录失败: {msg}", f"✗ ログイン失敗: {msg}"))
            signal_bus.log_message.emit(
                tr(
                    f"[Login failed] {msg}",
                    f"[登录失败] {msg}",
                    f"[ログイン失敗] {msg}",
                )
            )
            if not silent:
                InfoBar.error(
                    title=tr("Login failed", "登录失败", "ログイン失敗"),
                    content=msg or tr("Please check username and password", "请检查账号和密码", "ユーザー名とパスワードを確認してください"),
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=5000,
                    parent=self,
                )

    def _do_logout(self):
        download_manager.api.logout()
        download_manager.set_login(False)
        self._set_logged_in_ui(False, "")
        signal_bus.login_state_changed.emit(False)
        signal_bus.log_message.emit(tr("[Logout] Token cleared", "[退出登录] Token 已清除", "[ログアウト] Token を削除しました"))
        InfoBar.info(
            title=tr("Logged out", "已退出登录", "ログアウトしました"),
            content="",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=2000,
            parent=self,
        )

    def _on_auth_toggle(self, checked: bool):
        app_config.auth_enabled = checked
        self._cred_widget.setVisible(checked)
        if not checked:
            download_manager.set_login(False)
            self._set_logged_in_ui(False, "")
            signal_bus.login_state_changed.emit(False)
