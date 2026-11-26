"""Proxy and aria2 RPC cards."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    PasswordLineEdit,
    PrimaryPushButton,
    PushButton,
    SubtitleLabel,
    SwitchButton,
)

from ...config import DEFAULT_PROXY_URL, app_config
from ...presentation.settings import values as setting_values
from ...presentation.settings.texts import stext
from .page_dependencies import download_manager

_ARIA2_TEST_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="iwara-aria2-test")


class SettingsNetworkCardsMixin:
    """Proxy and aria2 RPC cards."""

    def _build_proxy_card(self):
        proxy_card = self._settings_board.create_card("proxy")
        proxy_layout = QVBoxLayout(proxy_card)
        proxy_layout.setContentsMargins(20, 16, 20, 16)
        proxy_layout.setSpacing(10)

        proxy_layout.addWidget(SubtitleLabel(stext("proxy"), proxy_card))
        proxy_layout.addWidget(
            BodyLabel(
                stext("api_proxy_is_used_for_login"),
                proxy_card,
            )
        )

        api_proxy_header = QHBoxLayout()
        api_proxy_header.addWidget(BodyLabel(stext("login_parsing_proxy"), proxy_card))
        api_proxy_header.addStretch()
        self._api_proxy_switch = SwitchButton(proxy_card)
        self._api_proxy_switch.checkedChanged.connect(self._on_api_proxy_toggle)
        api_proxy_header.addWidget(self._api_proxy_switch)
        proxy_layout.addLayout(api_proxy_header)

        self._api_proxy_widget = QWidget(proxy_card)
        api_proxy_inner = QHBoxLayout(self._api_proxy_widget)
        api_proxy_inner.setContentsMargins(0, 0, 0, 0)
        self._api_proxy_edit = LineEdit(self._api_proxy_widget)
        self._api_proxy_edit.setPlaceholderText(DEFAULT_PROXY_URL)
        self._api_proxy_edit.textChanged.connect(self._on_api_proxy_url_changed)
        api_proxy_inner.addWidget(self._api_proxy_edit, stretch=1)
        proxy_layout.addWidget(self._api_proxy_widget)

        download_proxy_header = QHBoxLayout()
        download_proxy_header.addWidget(BodyLabel(stext("video_download_proxy"), proxy_card))
        download_proxy_header.addStretch()
        self._download_proxy_switch = SwitchButton(proxy_card)
        self._download_proxy_switch.checkedChanged.connect(self._on_download_proxy_toggle)
        download_proxy_header.addWidget(self._download_proxy_switch)
        proxy_layout.addLayout(download_proxy_header)

        proxy_layout.addWidget(
            BodyLabel(
                stext("only_affects_large_file_downloads_and"),
                proxy_card,
            )
        )

        self._download_proxy_widget = QWidget(proxy_card)
        download_proxy_inner = QHBoxLayout(self._download_proxy_widget)
        download_proxy_inner.setContentsMargins(0, 0, 0, 0)
        self._download_proxy_edit = LineEdit(self._download_proxy_widget)
        self._download_proxy_edit.setPlaceholderText(DEFAULT_PROXY_URL)
        self._download_proxy_edit.textChanged.connect(self._on_download_proxy_url_changed)
        download_proxy_inner.addWidget(self._download_proxy_edit, stretch=1)
        proxy_layout.addWidget(self._download_proxy_widget)

        apply_proxy_btn = PrimaryPushButton(stext("apply"), proxy_card)
        apply_proxy_btn.setFixedWidth(80)
        apply_proxy_btn.clicked.connect(self._apply_proxy)

        proxy_apply_row = QHBoxLayout()
        proxy_apply_row.addStretch()
        proxy_apply_row.addWidget(apply_proxy_btn)
        proxy_layout.addLayout(proxy_apply_row)
        self._settings_board.add_card("proxy", proxy_card)

    def _build_aria2_card(self):
        aria2_card = self._settings_board.create_card("aria2")
        aria2_layout = QVBoxLayout(aria2_card)
        aria2_layout.setContentsMargins(20, 16, 20, 16)
        aria2_layout.setSpacing(10)

        aria2_header = QHBoxLayout()
        aria2_header.addWidget(SubtitleLabel("Aria2 RPC", aria2_card))
        aria2_header.addStretch()
        self._aria2_switch = SwitchButton(aria2_card)
        self._aria2_switch.checkedChanged.connect(self._on_aria2_toggle)
        aria2_header.addWidget(self._aria2_switch)
        aria2_layout.addLayout(aria2_header)

        aria2_layout.addWidget(
            BodyLabel(
                stext("when_enabled_downloads_are_delegated_to"),
                aria2_card,
            )
        )

        self._aria2_widget = QWidget(aria2_card)
        aria2_inner = QVBoxLayout(self._aria2_widget)
        aria2_inner.setContentsMargins(0, 0, 0, 0)
        aria2_inner.setSpacing(8)

        self._aria2_url_edit = LineEdit(self._aria2_widget)
        self._aria2_url_edit.setPlaceholderText("http://127.0.0.1:6800/jsonrpc")
        aria2_inner.addWidget(self._aria2_url_edit)

        self._aria2_token_edit = PasswordLineEdit(self._aria2_widget)
        self._aria2_token_edit.setPlaceholderText(stext("rpc_token_optional"))
        aria2_inner.addWidget(self._aria2_token_edit)
        self._build_aria2_remote_section(aria2_inner)

        aria2_layout.addWidget(self._aria2_widget)
        self._settings_board.add_card("aria2", aria2_card)

    def _build_aria2_remote_section(self, layout: QVBoxLayout):
        """aria2 on a NAS / server: where it saves, and how this machine reaches those files."""

        parent = self._aria2_widget
        header = QHBoxLayout()
        header.addWidget(BodyLabel(stext("aria2_runs_on_another_device_nas"), parent))
        header.addStretch()
        self._aria2_remote_switch = SwitchButton(parent)
        self._aria2_remote_switch.checkedChanged.connect(self._on_aria2_remote_toggle)
        header.addWidget(self._aria2_remote_switch)
        layout.addLayout(header)

        self._aria2_remote_widget = QWidget(parent)
        inner = QVBoxLayout(self._aria2_remote_widget)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setSpacing(6)
        hint = CaptionLabel(stext("videos_are_saved_by_aria2_on"), self._aria2_remote_widget)
        hint.setWordWrap(True)
        inner.addWidget(hint)

        self._aria2_remote_dir_edit = LineEdit(self._aria2_remote_widget)
        self._aria2_remote_dir_edit.setPlaceholderText(stext("download_folder_on_the_aria2_device"))
        inner.addWidget(self._aria2_remote_dir_edit)

        self._aria2_remote_mount_edit = LineEdit(self._aria2_remote_widget)
        self._aria2_remote_mount_edit.setPlaceholderText(stext("same_folder_mounted_on_this_machine"))
        inner.addWidget(self._aria2_remote_mount_edit)

        self._aria2_remote_http_edit = LineEdit(self._aria2_remote_widget)
        self._aria2_remote_http_edit.setPlaceholderText(stext("http_address_serving_that_folder_for"))
        inner.addWidget(self._aria2_remote_http_edit)

        test_row = QHBoxLayout()
        test_row.addStretch()
        self._aria2_test_btn = PushButton(stext("test_connection"), self._aria2_remote_widget)
        self._aria2_test_btn.clicked.connect(self._test_aria2_connection)
        test_row.addWidget(self._aria2_test_btn)
        inner.addLayout(test_row)
        layout.addWidget(self._aria2_remote_widget)

    def _on_aria2_remote_toggle(self, checked: bool):
        app_config.aria2_remote_enabled = checked
        self._aria2_remote_widget.setVisible(checked)

    def _save_aria2_settings(self):
        setting_values.save_aria2(
            enabled=self._aria2_switch.isChecked(),
            url=self._aria2_url_edit.text(),
            token=self._aria2_token_edit.text(),
            remote=self._aria2_remote_switch.isChecked(),
            remote_dir=self._aria2_remote_dir_edit.text(),
            mount_dir=self._aria2_remote_mount_edit.text(),
            http_base=self._aria2_remote_http_edit.text(),
        )

    def _test_aria2_connection(self):
        """Ask aria2 for its version off the GUI thread, then report what is set up."""

        self._save_aria2_settings()
        self._aria2_test_btn.setEnabled(False)
        future = _ARIA2_TEST_POOL.submit(download_manager._aria2_rpc_call, "aria2.getVersion", [])
        timer = QTimer(self)

        def poll():
            if not future.done():
                return
            timer.stop()
            timer.deleteLater()
            self._aria2_test_btn.setEnabled(True)
            try:
                data, error = future.result()
            except Exception as exc:  # pragma: no cover - defensive
                data, error = None, str(exc)
            self._report_aria2_test(data, error)

        timer.timeout.connect(poll)
        timer.start(100)

    def _report_aria2_test(self, data, error: str):
        ok, title, content = setting_values.aria2_test_report(data, error)
        (InfoBar.success if ok else InfoBar.error)(
            title=title,
            content=content,
            orient=Qt.Orientation.Horizontal, isClosable=True,
            position=InfoBarPosition.TOP, duration=6000 if ok else 5000, parent=self,
        )

    def _on_api_proxy_toggle(self, checked: bool):
        self._api_proxy_widget.setVisible(checked)
        setting_values.set_api_proxy_enabled(checked, download_manager)

    def _on_api_proxy_url_changed(self, text: str):
        app_config.api_proxy_url = text

    def _on_download_proxy_toggle(self, checked: bool):
        app_config.download_proxy_enabled = checked
        self._download_proxy_widget.setVisible(checked)

    def _on_download_proxy_url_changed(self, text: str):
        app_config.download_proxy_url = text

    def _on_aria2_toggle(self, checked: bool):
        app_config.aria2_rpc_enabled = checked
        self._aria2_widget.setVisible(checked)

    def _apply_proxy(self):
        title, content = setting_values.apply_proxies(
            download_manager,
            api_enabled=self._api_proxy_switch.isChecked(),
            api_url=self._api_proxy_edit.text(),
            download_enabled=self._download_proxy_switch.isChecked(),
            download_url=self._download_proxy_edit.text(),
        )
        self._api_proxy_edit.setText(app_config.api_proxy_url)
        self._download_proxy_edit.setText(app_config.download_proxy_url)
        InfoBar.success(
            title=title,
            content=content,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=3000,
            parent=self,
        )
