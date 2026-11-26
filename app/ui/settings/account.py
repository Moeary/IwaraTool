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
from ...core import account
from ...i18n import tr
from ...presentation.settings import values as setting_values
from ...presentation.settings.texts import stext
from ...signal_bus import signal_bus
from ..common.worker_lifecycle import ManagedThread
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
        login_header.addWidget(SubtitleLabel(stext("account_login"), login_card))
        login_header.addStretch()
        self._auth_switch = SwitchButton(login_card)
        self._auth_switch.checkedChanged.connect(self._on_auth_toggle)
        login_header.addWidget(self._auth_switch)
        login_layout.addLayout(login_header)

        login_layout.addWidget(
            BodyLabel(
                stext("private_videos_require_login"),
                login_card,
            )
        )

        self._cred_widget = QWidget(login_card)
        cred_layout = QVBoxLayout(self._cred_widget)
        cred_layout.setContentsMargins(0, 4, 0, 0)
        cred_layout.setSpacing(8)

        self._user_edit = LineEdit(self._cred_widget)
        self._user_edit.setPlaceholderText(stext("username"))
        self._user_edit.setClearButtonEnabled(True)

        self._pass_edit = PasswordLineEdit(self._cred_widget)
        self._pass_edit.setPlaceholderText(stext("password"))

        btn_row = QHBoxLayout()
        self._login_btn = PrimaryPushButton(stext("login"), self._cred_widget, FluentIcon.PEOPLE)
        self._login_btn.setFixedWidth(100)
        self._login_btn.clicked.connect(lambda: self._do_login(silent=False))

        self._logout_btn = PushButton(stext("logout"), self._cred_widget, FluentIcon.CANCEL)
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
            self._login_status_lbl.setText(status_text or stext("signed_in"))
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
                    title=stext("notice"),
                    content=stext("please_enter_username_email_and_password"),
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=3000,
                    parent=self,
                )
            return

        if not silent and not app_config.api_proxy_enabled:
            InfoBar.warning(
                title=stext("api_proxy_is_off"),
                content=stext("if_tun_system_proxy_is_not"),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=5000,
                parent=self,
            )

        if self._worker is not None and self._worker.isRunning():
            return  # a sign-in is already on its way
        self._login_btn.setEnabled(False)
        self._login_status_lbl.setText(stext("signing_in"))
        signal_bus.log_message.emit(
            stext("login_signing_in")
        )

        download_manager.apply_config()
        worker = LoginWorker(credential, password)
        worker.result_ready.connect(lambda ok, msg: self._on_login_finished(ok, msg, silent))
        worker.finished.connect(lambda worker=worker: self._release_login_worker(worker))
        self._worker = worker
        download_manager.set_login_pending(True)
        try:
            worker.start()
        except Exception:
            self._worker = None
            download_manager.set_login_pending(False)
            self._login_btn.setEnabled(True)
            raise

    def _release_login_worker(self, worker: LoginWorker):
        if self._worker is worker:
            self._worker = None
            download_manager.set_login_pending(False)
        worker.deleteLater()

    def _on_login_finished(self, ok: bool, msg: str, silent: bool):
        self._login_btn.setEnabled(True)
        if ok:
            self._set_logged_in_ui(True)
            self._auth_switch.setChecked(True)
            account.complete_login(download_manager, self._user_edit.text(), self._pass_edit.text())
            if not silent:
                InfoBar.success(
                    title=stext("login_successful"),
                    content=stext("signed_in_2"),
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=3000,
                    parent=self,
                )
        else:
            self._set_logged_in_ui(False, setting_values.login_failed_text(msg))
            signal_bus.log_message.emit(
                tr(
                    f"[Login failed] {msg}",
                    f"[登录失败] {msg}",
                    f"[ログイン失敗] {msg}",
                )
            )
            if not silent:
                InfoBar.error(
                    title=stext("login_failed"),
                    content=msg or stext("please_check_username_and_password"),
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=5000,
                    parent=self,
                )

    def _do_logout(self):
        self._set_logged_in_ui(False, "")
        account.logout(download_manager)
        InfoBar.info(
            title=stext("logged_out"),
            content="",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=2000,
            parent=self,
        )

    def _on_auth_toggle(self, checked: bool):
        self._cred_widget.setVisible(checked)
        setting_values.set_auth_enabled(checked, download_manager)
        if not checked:
            self._set_logged_in_ui(False, "")
