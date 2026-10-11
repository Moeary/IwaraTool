"""Proxy and aria2 RPC cards."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import (
    BodyLabel,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    PasswordLineEdit,
    PrimaryPushButton,
    SubtitleLabel,
    SwitchButton,
)

from ...config import app_config
from ...i18n import tr
from .page_dependencies import download_manager


class SettingsNetworkCardsMixin:
    """Proxy and aria2 RPC cards."""

    def _build_proxy_card(self):
        proxy_card = self._settings_board.create_card("proxy")
        proxy_layout = QVBoxLayout(proxy_card)
        proxy_layout.setContentsMargins(20, 16, 20, 16)
        proxy_layout.setSpacing(10)

        proxy_layout.addWidget(SubtitleLabel(tr("Proxy", "代理设置", "プロキシ"), proxy_card))
        proxy_layout.addWidget(
            BodyLabel(
                tr(
                    "API proxy is used for login, URL parsing, subscriptions and metadata. If TUN mode is off, keep this enabled and make sure your local proxy port is running.",
                    "API 代理用于登录、链接解析、订阅和元数据请求。未开启 TUN 时建议保持开启，并确认本机代理端口正在运行。",
                    "API プロキシはログイン、URL 解析、購読、メタデータ取得に使います。TUN が無効な場合は有効にし、ローカルプロキシポートが起動していることを確認してください。",
                ),
                proxy_card,
            )
        )

        api_proxy_header = QHBoxLayout()
        api_proxy_header.addWidget(BodyLabel(tr("Login / parsing proxy", "登录/解析代理", "ログイン/解析プロキシ"), proxy_card))
        api_proxy_header.addStretch()
        self._api_proxy_switch = SwitchButton(proxy_card)
        self._api_proxy_switch.checkedChanged.connect(self._on_api_proxy_toggle)
        api_proxy_header.addWidget(self._api_proxy_switch)
        proxy_layout.addLayout(api_proxy_header)

        self._api_proxy_widget = QWidget(proxy_card)
        api_proxy_inner = QHBoxLayout(self._api_proxy_widget)
        api_proxy_inner.setContentsMargins(0, 0, 0, 0)
        self._api_proxy_edit = LineEdit(self._api_proxy_widget)
        self._api_proxy_edit.setPlaceholderText("http://127.0.0.1:7890")
        self._api_proxy_edit.textChanged.connect(self._on_api_proxy_url_changed)
        api_proxy_inner.addWidget(self._api_proxy_edit, stretch=1)
        proxy_layout.addWidget(self._api_proxy_widget)

        download_proxy_header = QHBoxLayout()
        download_proxy_header.addWidget(BodyLabel(tr("Video download proxy", "视频下载代理", "動画ダウンロードプロキシ"), proxy_card))
        download_proxy_header.addStretch()
        self._download_proxy_switch = SwitchButton(proxy_card)
        self._download_proxy_switch.checkedChanged.connect(self._on_download_proxy_toggle)
        download_proxy_header.addWidget(self._download_proxy_switch)
        proxy_layout.addLayout(download_proxy_header)

        proxy_layout.addWidget(
            BodyLabel(
                tr(
                    "Only affects large file downloads and aria2 all-proxy; thumbnails and metadata still follow the API proxy.",
                    "仅影响大文件下载和 aria2 all-proxy；封面、头像和元数据仍跟随 API 代理。",
                    "大きなファイルのダウンロードと aria2 all-proxy のみに影響します。サムネイル、アバター、メタデータは API プロキシに従います。",
                ),
                proxy_card,
            )
        )

        self._download_proxy_widget = QWidget(proxy_card)
        download_proxy_inner = QHBoxLayout(self._download_proxy_widget)
        download_proxy_inner.setContentsMargins(0, 0, 0, 0)
        self._download_proxy_edit = LineEdit(self._download_proxy_widget)
        self._download_proxy_edit.setPlaceholderText("http://127.0.0.1:7890")
        self._download_proxy_edit.textChanged.connect(self._on_download_proxy_url_changed)
        download_proxy_inner.addWidget(self._download_proxy_edit, stretch=1)
        proxy_layout.addWidget(self._download_proxy_widget)

        apply_proxy_btn = PrimaryPushButton(tr("Apply", "应用", "適用"), proxy_card)
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
                tr(
                    "When enabled, downloads are delegated to aria2 RPC",
                    "启用后下载任务交由 aria2 RPC 代理处理",
                    "有効時、ダウンロードは aria2 RPC に委譲されます",
                ),
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
        self._aria2_token_edit.setPlaceholderText(tr("RPC token, optional", "RPC token，可留空", "RPC token、任意"))
        aria2_inner.addWidget(self._aria2_token_edit)

        aria2_layout.addWidget(self._aria2_widget)
        self._settings_board.add_card("aria2", aria2_card)

    def _on_api_proxy_toggle(self, checked: bool):
        app_config.api_proxy_enabled = checked
        self._api_proxy_widget.setVisible(checked)
        download_manager.apply_config()
        if not checked:
            download_manager.api.set_proxy("")

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
        app_config.api_proxy_enabled = self._api_proxy_switch.isChecked()
        app_config.api_proxy_url = self._api_proxy_edit.text().strip() or "http://127.0.0.1:7890"
        app_config.download_proxy_enabled = self._download_proxy_switch.isChecked()
        app_config.download_proxy_url = self._download_proxy_edit.text().strip() or "http://127.0.0.1:7890"
        self._api_proxy_edit.setText(app_config.api_proxy_url)
        self._download_proxy_edit.setText(app_config.download_proxy_url)
        download_manager.apply_config()
        api_state = tr("On", "开", "オン") if app_config.api_proxy_enabled else tr("Off", "关", "オフ")
        download_state = tr("On", "开", "オン") if app_config.download_proxy_enabled else tr("Off", "关", "オフ")
        InfoBar.success(
            title=tr("Proxy Applied", "代理已应用", "プロキシを適用しました"),
            content=tr(
                f"API: {api_state} {app_config.api_proxy_url}; Download: {download_state} {app_config.download_proxy_url}",
                f"API：{api_state} {app_config.api_proxy_url}；下载：{download_state} {app_config.download_proxy_url}",
                f"API: {api_state} {app_config.api_proxy_url}; ダウンロード: {download_state} {app_config.download_proxy_url}",
            ),
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=3000,
            parent=self,
        )
