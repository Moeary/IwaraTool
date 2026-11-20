"""Proxy and aria2 RPC cards."""

from __future__ import annotations

import os
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
from ...i18n import tr
from .page_dependencies import download_manager

_ARIA2_TEST_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="iwara-aria2-test")


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
                    "API proxy is used for login, URL parsing, subscriptions and metadata. While it is off, requests follow the system proxy / TUN. "
                    "If neither is on, enable it and enter your local proxy port (Clash Verge Rev uses 7897, older CFW setups 7890).",
                    "API 代理用于登录、链接解析、订阅和元数据请求。关闭时请求跟随系统代理 / TUN；两者都未开启时，请启用并填写本机代理端口"
                    "（Clash Verge Rev 默认 7897，旧版 CFW 常用 7890）。",
                    "API プロキシはログイン、URL 解析、購読、メタデータ取得に使います。無効の間はシステムプロキシ / TUN に従います。"
                    "どちらも無効な場合は有効にしてローカルのプロキシポートを入力してください（Clash Verge Rev は 7897、旧 CFW は 7890）。",
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
        self._api_proxy_edit.setPlaceholderText(DEFAULT_PROXY_URL)
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
        self._download_proxy_edit.setPlaceholderText(DEFAULT_PROXY_URL)
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
        self._build_aria2_remote_section(aria2_inner)

        aria2_layout.addWidget(self._aria2_widget)
        self._settings_board.add_card("aria2", aria2_card)

    def _build_aria2_remote_section(self, layout: QVBoxLayout):
        """aria2 on a NAS / server: where it saves, and how this machine reaches those files."""

        parent = self._aria2_widget
        header = QHBoxLayout()
        header.addWidget(BodyLabel(tr(
            "aria2 runs on another device (NAS / server)",
            "aria2 运行在其他设备上（NAS / 服务器）",
            "aria2 は別のデバイスで動作（NAS / サーバー）",
        ), parent))
        header.addStretch()
        self._aria2_remote_switch = SwitchButton(parent)
        self._aria2_remote_switch.checkedChanged.connect(self._on_aria2_remote_toggle)
        header.addWidget(self._aria2_remote_switch)
        layout.addLayout(header)

        self._aria2_remote_widget = QWidget(parent)
        inner = QVBoxLayout(self._aria2_remote_widget)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setSpacing(6)
        hint = CaptionLabel(tr(
            "Videos are saved by aria2 on that device, not in the local download folder. History marks them as \"On NAS\" instead of local. "
            "Covers are cached on this machine; NFO and covers go next to the video only when the folder is mounted here.",
            "视频由该设备上的 aria2 直接保存，不会写入本机下载目录；历史记录会标为「远端」而不是本地。"
            "封面缓存在本机；只有填写了本机挂载路径时，NFO 与封面才会写到视频旁边。",
            "動画はそのデバイスの aria2 が保存し、ローカルの保存先には書き込みません。履歴では「リモート」と表示されます。"
            "カバーはこの PC にキャッシュし、NFO とカバーはマウント先がある場合のみ動画の隣に保存します。",
        ), self._aria2_remote_widget)
        hint.setWordWrap(True)
        inner.addWidget(hint)

        self._aria2_remote_dir_edit = LineEdit(self._aria2_remote_widget)
        self._aria2_remote_dir_edit.setPlaceholderText(tr(
            "Download folder on the aria2 device, e.g. /volume1/iwara (required)",
            "aria2 设备上的下载目录，如 /volume1/iwara（必填）",
            "aria2 デバイス上の保存先（例: /volume1/iwara、必須）",
        ))
        inner.addWidget(self._aria2_remote_dir_edit)

        self._aria2_remote_mount_edit = LineEdit(self._aria2_remote_widget)
        self._aria2_remote_mount_edit.setPlaceholderText(tr(
            "Same folder mounted on this machine (SMB / NFS), e.g. /Volumes/iwara or Z:\\iwara (optional)",
            "该目录在本机的挂载路径（SMB / NFS），如 /Volumes/iwara 或 Z:\\iwara（可选）",
            "このフォルダーのローカルマウント先（SMB / NFS、例: /Volumes/iwara や Z:\\iwara、任意）",
        ))
        inner.addWidget(self._aria2_remote_mount_edit)

        self._aria2_remote_http_edit = LineEdit(self._aria2_remote_widget)
        self._aria2_remote_http_edit.setPlaceholderText(tr(
            "HTTP address serving that folder, for streaming, e.g. http://nas:8080/iwara (optional)",
            "提供该目录访问的 HTTP 地址，用于串流播放，如 http://nas:8080/iwara（可选）",
            "このフォルダーを配信する HTTP アドレス（ストリーミング用、例: http://nas:8080/iwara、任意）",
        ))
        inner.addWidget(self._aria2_remote_http_edit)

        test_row = QHBoxLayout()
        test_row.addStretch()
        self._aria2_test_btn = PushButton(tr("Test connection", "测试连接", "接続テスト"), self._aria2_remote_widget)
        self._aria2_test_btn.clicked.connect(self._test_aria2_connection)
        test_row.addWidget(self._aria2_test_btn)
        inner.addLayout(test_row)
        layout.addWidget(self._aria2_remote_widget)

    def _on_aria2_remote_toggle(self, checked: bool):
        app_config.aria2_remote_enabled = checked
        self._aria2_remote_widget.setVisible(checked)

    def _save_aria2_settings(self):
        app_config.aria2_rpc_enabled = self._aria2_switch.isChecked()
        app_config.aria2_rpc_url = self._aria2_url_edit.text().strip()
        app_config.aria2_rpc_token = self._aria2_token_edit.text().strip()
        app_config.aria2_remote_enabled = self._aria2_remote_switch.isChecked()
        app_config.aria2_remote_dir = self._aria2_remote_dir_edit.text()
        app_config.aria2_remote_mount_dir = self._aria2_remote_mount_edit.text()
        app_config.aria2_remote_http_base = self._aria2_remote_http_edit.text()

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
        if not data:
            InfoBar.error(
                title=tr("aria2 not reachable", "无法连接 aria2", "aria2 に接続できません"),
                content=str(error or ""),
                orient=Qt.Orientation.Horizontal, isClosable=True,
                position=InfoBarPosition.TOP, duration=5000, parent=self,
            )
            return
        version = str((data.get("result") or {}).get("version", "") or "?")
        notes = [tr(f"aria2 {version} is reachable.", f"已连接 aria2 {version}。", f"aria2 {version} に接続しました。")]
        if app_config.aria2_remote_enabled:
            if not app_config.aria2_remote_dir:
                notes.append(tr(
                    "Fill in the download folder on the aria2 device.",
                    "请填写 aria2 设备上的下载目录。",
                    "aria2 デバイス上の保存先を入力してください。",
                ))
            mount = app_config.aria2_remote_mount_dir
            if mount and not os.path.isdir(mount):
                notes.append(tr(
                    f"The local mount folder is not reachable now: {mount}",
                    f"本机挂载路径当前不可访问：{mount}",
                    f"マウント先に現在アクセスできません: {mount}",
                ))
            elif not mount and not app_config.aria2_remote_http_base:
                notes.append(tr(
                    "Without a mount folder or HTTP address, NAS copies cannot be played or opened here.",
                    "未填写挂载路径或 HTTP 地址时，本机无法播放或打开远端文件。",
                    "マウント先か HTTP アドレスがないと、リモートのファイルはここで再生できません。",
                ))
        InfoBar.success(
            title=tr("Connection OK", "连接成功", "接続成功"),
            content=" ".join(notes),
            orient=Qt.Orientation.Horizontal, isClosable=True,
            position=InfoBarPosition.TOP, duration=6000, parent=self,
        )

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
        app_config.api_proxy_url = self._api_proxy_edit.text().strip() or DEFAULT_PROXY_URL
        app_config.download_proxy_enabled = self._download_proxy_switch.isChecked()
        app_config.download_proxy_url = self._download_proxy_edit.text().strip() or DEFAULT_PROXY_URL
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
