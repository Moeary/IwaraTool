"""Settings Interface — login, quality, download dir, concurrency, proxy."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QMessageBox, QVBoxLayout, QWidget

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
        self._loading_settings = False

        self._content = QWidget(self)
        self._content.setObjectName("settingsContent")
        self.setWidget(self._content)
        self.setWidgetResizable(True)

        self._build_ui()
        self._load_settings()

        # Startup auth: prefer cached token for faster boot; fallback to credential login.
        if download_manager.restore_cached_login():
            self._set_logged_in_ui(True, tr("✓ Signed in (cached token)", "✓ 已登录（已加载本地 Token）", "✓ ログイン済み（ローカルトークン使用）"))
            signal_bus.login_state_changed.emit(True)
        elif app_config.auth_enabled and app_config.username and app_config.password:
            self._do_login(silent=True)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self._content)
        layout.setContentsMargins(36, 24, 36, 24)
        layout.setSpacing(16)

        layout.addWidget(TitleLabel(tr("Settings", "应用设置", "設定"), self._content))

        # ── Language ─────────────────────────────────────────────────────────
        lang_card = CardWidget(self._content)
        lang_layout = QVBoxLayout(lang_card)
        lang_layout.setContentsMargins(20, 16, 20, 16)
        lang_layout.setSpacing(10)

        lang_layout.addWidget(SubtitleLabel(tr("Interface Language", "界面语言", "表示言語"), lang_card))
        lang_layout.addWidget(
            BodyLabel(
                tr(
                    "Default is Chinese; language switch is applied immediately",
                    "默认中文；切换后立即生效",
                    "初期値は中国語です。言語切替は即時反映されます",
                ),
                lang_card,
            )
        )

        lang_row = QHBoxLayout()
        self._lang_combo = ComboBox(lang_card)
        self._lang_combo.addItems(["简体中文", "English", "日本語"])
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
        data_layout.addWidget(SubtitleLabel(tr("Local Data Paths (Portable)", "本地数据位置（绿色模式）", "ローカルデータパス（ポータブル）"), data_card))
        data_layout.addWidget(BodyLabel(f"{tr('Data dir', '数据目录', 'データディレクトリ')}: {app_config.app_data_dir}", data_card))
        data_layout.addWidget(BodyLabel(f"{tr('Config file', '配置文件', '設定ファイル')}: {app_config.config_path}", data_card))
        data_layout.addWidget(BodyLabel(f"{tr('History DB', '下载历史库', '履歴DB')}: {app_config.history_db_path}", data_card))
        layout.addWidget(data_card)

        # ── Account / Login card ──────────────────────────────────────────────
        login_card = CardWidget(self._content)
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
                    "Private videos require login. Username/password and token are saved locally; startup prefers cached token for faster sign-in. Username + password is recommended.",
                    "登录后可下载私有视频；账号密码和 token 在本地持久化保存，启动时会优先使用 token 加速登录。建议使用用户名+密码登录，邮箱登录可能偶发失败。",
                    "非公開動画の取得にはログインが必要です。ユーザー名/パスワードと token はローカル保存され、起動時は token 優先で高速ログインします。ユーザー名+パスワード推奨です。",
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

        self._logout_btn = PrimaryPushButton(tr("Logout", "退出登录", "ログアウト"), self._cred_widget, FluentIcon.CANCEL)
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

        quality_layout.addWidget(SubtitleLabel(tr("Preferred Quality", "下载画质偏好", "優先画質"), quality_card))
        quality_layout.addWidget(
            BodyLabel(
                tr(
                    "If preferred quality is unavailable, fallback order is Source > 540 > 360",
                    "首选画质不存在时自动回退到更低分辨率：Source > 540 > 360",
                    "優先画質が無い場合は Source > 540 > 360 の順で自動フォールバックします",
                ),
                quality_card,
            )
        )

        quality_row = QHBoxLayout()
        self._quality_combo = ComboBox(quality_card)
        self._quality_combo.addItems(["Source", "540p", "360p"])
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

        dir_layout.addWidget(SubtitleLabel(tr("Download Directory", "下载目录", "ダウンロード先"), dir_card))
        dir_layout.addWidget(
            BodyLabel(
                tr(
                    "Video files are saved here (auto subfolder by author)",
                    "视频文件保存位置（自动按作者名建立子文件夹）",
                    "動画保存先（作者名で自動サブフォルダー作成）",
                ),
                dir_card,
            )
        )

        dir_row = QHBoxLayout()
        self._dir_edit = LineEdit(dir_card)
        self._dir_edit.setPlaceholderText(tr("Choose download directory…", "选择下载目录…", "保存先を選択…"))
        self._dir_edit.setReadOnly(True)

        browse_btn = ToolButton(FluentIcon.FOLDER, dir_card)
        browse_btn.setToolTip(tr("Browse…", "浏览…", "参照…"))
        browse_btn.clicked.connect(self._browse_dir)

        dir_row.addWidget(self._dir_edit, stretch=1)
        dir_row.addWidget(browse_btn)
        dir_layout.addLayout(dir_row)

        cleanup_row = QHBoxLayout()
        cleanup_row.addWidget(
            BodyLabel(
                tr(
                    "Clean stale *_temp files in download directory",
                    "清理下载目录中残留的 *_temp 临时文件",
                    "ダウンロード先の *_temp 残留ファイルを削除",
                ),
                dir_card,
            )
        )
        cleanup_row.addStretch()
        cleanup_btn = PrimaryPushButton(tr("Clean *_temp", "清理 _temp", "_temp を削除"), dir_card, FluentIcon.DELETE)
        cleanup_btn.setFixedWidth(140)
        cleanup_btn.clicked.connect(self._confirm_clear_temp_files)
        cleanup_row.addWidget(cleanup_btn)
        dir_layout.addLayout(cleanup_row)
        layout.addWidget(dir_card)

        # ── Filename template & de-dup ───────────────────────────────────────
        name_card = CardWidget(self._content)
        name_layout = QVBoxLayout(name_card)
        name_layout.setContentsMargins(20, 16, 20, 16)
        name_layout.setSpacing(10)

        name_layout.addWidget(SubtitleLabel(tr("Filename Template", "下载命名规则", "ファイル名テンプレート"), name_card))
        name_layout.addWidget(
            BodyLabel(
                tr(
                    "Placeholders: {username} {author} {YYYY-MM-DD} {YYYY} {MM} {DD} {title} {id} {quality} {views} {likes} {comments} {duration} {slug} {rating}; ",
                    "可用占位符：{username} {author} {YYYY-MM-DD} {YYYY} {MM} {DD} {title} {id} {quality} {views} {likes} {comments} {duration} {slug} {rating}",
                    "使用可能プレースホルダー: {username} {author} {YYYY-MM-DD} {YYYY} {MM} {DD} {title} {id} {quality} {views} {likes} {comments} {duration} {slug} {rating}",
                ),
                name_card,
            )
        )
        name_layout.addWidget(
            BodyLabel(
                tr(
                    "default {username}/{YYYY-MM-DD}_{title}_{id}.mp4",
                    "默认 {username}/{YYYY-MM-DD}_{title}_{id}.mp4",
                    "既定値 {username}/{YYYY-MM-DD}_{title}_{id}.mp4",
                ),
                name_card,
            )
        )

        self._name_tpl_edit = LineEdit(name_card)
        self._name_tpl_edit.setPlaceholderText("{username}/{YYYY-MM-DD}_{title}_{id}.mp4")
        self._name_tpl_edit.setClearButtonEnabled(True)
        name_layout.addWidget(self._name_tpl_edit)

        skip_row = QHBoxLayout()
        skip_row.addWidget(
            BodyLabel(
                tr(
                    "Skip existing completed files in batch mode",
                    "批量下载时跳过下载目录中已存在的完整文件",
                    "一括時に既存の完了ファイルをスキップ",
                ),
                name_card,
            )
        )
        skip_row.addStretch()
        self._skip_existing_switch = SwitchButton(name_card)
        skip_row.addWidget(self._skip_existing_switch)
        name_layout.addLayout(skip_row)

        cover_row = QHBoxLayout()
        cover_row.addWidget(
            BodyLabel(
                tr(
                    "Save video thumbnail after success (.jpg)",
                    "下载成功后同时保存视频封面（同名 .jpg）",
                    "完了後にサムネイルを保存（同名 .jpg）",
                ),
                name_card,
            )
        )
        cover_row.addStretch()
        self._download_thumb_switch = SwitchButton(name_card)
        cover_row.addWidget(self._download_thumb_switch)
        name_layout.addLayout(cover_row)

        nfo_row = QHBoxLayout()
        nfo_row.addWidget(
            BodyLabel(
                tr(
                    "Generate sidecar .nfo metadata after success",
                    "下载成功后生成同名 .nfo 元数据文件",
                    "完了後に同名 .nfo メタデータを生成",
                ),
                name_card,
            )
        )
        nfo_row.addStretch()
        self._collect_nfo_switch = SwitchButton(name_card)
        nfo_row.addWidget(self._collect_nfo_switch)
        name_layout.addLayout(nfo_row)

        click_row = QHBoxLayout()
        click_row.addWidget(BodyLabel(tr("Completed card click action", "已完成任务卡片单击行为", "完了カードのクリック動作"), name_card))
        click_row.addStretch()
        self._completed_click_combo = ComboBox(name_card)
        self._completed_click_combo.addItems(
            [
                tr("Open Folder", "打开文件夹", "フォルダーを開く"),
                tr("Open Player", "打开视频播放器", "プレイヤーで開く"),
            ]
        )
        self._completed_click_combo.setFixedWidth(180)
        click_row.addWidget(self._completed_click_combo)
        name_layout.addLayout(click_row)

        layout.addWidget(name_card)

        # ── Concurrency ───────────────────────────────────────────────────────
        conc_card = CardWidget(self._content)
        conc_layout = QVBoxLayout(conc_card)
        conc_layout.setContentsMargins(20, 16, 20, 16)
        conc_layout.setSpacing(10)

        conc_layout.addWidget(SubtitleLabel(tr("Concurrent Downloads", "并发下载数", "同時ダウンロード数"), conc_card))
        conc_layout.addWidget(
            BodyLabel(
                tr(
                    "Max tasks in resolving/queued/downloading states (1-10)",
                    "同时处于解析/等待/下载状态的最大任务数（1–10）",
                    "解析/待機/ダウンロード中の最大タスク数（1-10）",
                ),
                conc_card,
            )
        )

        conc_row = QHBoxLayout()
        self._conc_slider = Slider(Qt.Orientation.Horizontal, conc_card)
        self._conc_slider.setRange(1, 10)
        self._conc_slider.valueChanged.connect(self._on_concurrency_changed)
        self._conc_slider.setMinimumWidth(420)
        self._conc_slider.setMaximumWidth(10000)

        self._conc_input = LineEdit(conc_card)
        self._conc_input.setFixedWidth(72)
        self._conc_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._conc_input.setValidator(QIntValidator(1, 10, self._conc_input))
        self._conc_input.setPlaceholderText("1-10")
        self._conc_input.editingFinished.connect(self._on_concurrency_input_finished)

        conc_row.addWidget(self._conc_slider, 8)
        conc_row.addSpacing(12)
        conc_row.addWidget(self._conc_input, 2)
        conc_layout.addLayout(conc_row)
        layout.addWidget(conc_card)

        # ── Search download limit ───────────────────────────────────────────
        search_card = CardWidget(self._content)
        search_layout = QVBoxLayout(search_card)
        search_layout.setContentsMargins(20, 16, 20, 16)
        search_layout.setSpacing(10)

        search_header = QHBoxLayout()
        search_header.addWidget(SubtitleLabel(tr("Search Download Limit", "搜索下载上限", "検索ダウンロード上限"), search_card))
        search_header.addStretch()
        self._search_limit_switch = SwitchButton(search_card)
        self._search_limit_switch.checkedChanged.connect(self._on_search_limit_toggle)
        search_header.addWidget(self._search_limit_switch)
        search_layout.addLayout(search_header)

        search_layout.addWidget(
            BodyLabel(
                tr(
                    "Applies to API search URLs like api.iwara.tv/videos?...",
                    "作用于 API 搜索链接（如 api.iwara.tv/videos?...）",
                    "API 検索URL（api.iwara.tv/videos?...）に適用されます",
                ),
                search_card,
            )
        )

        search_row = QHBoxLayout()
        search_row.addWidget(BodyLabel(tr("Max videos", "最大视频数", "最大動画数"), search_card))
        self._search_limit_edit = LineEdit(search_card)
        self._search_limit_edit.setFixedWidth(100)
        self._search_limit_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._search_limit_edit.setValidator(QIntValidator(1, 5000, self._search_limit_edit))
        self._search_limit_edit.setPlaceholderText("1-5000")
        self._search_limit_edit.editingFinished.connect(self._on_search_limit_input_finished)
        search_row.addWidget(self._search_limit_edit)
        search_row.addStretch()
        search_layout.addLayout(search_row)
        layout.addWidget(search_card)

        # ── Proxy ─────────────────────────────────────────────────────────────
        proxy_card = CardWidget(self._content)
        proxy_layout = QVBoxLayout(proxy_card)
        proxy_layout.setContentsMargins(20, 16, 20, 16)
        proxy_layout.setSpacing(10)

        proxy_header = QHBoxLayout()
        proxy_header.addWidget(SubtitleLabel(tr("Proxy", "代理设置", "プロキシ"), proxy_card))
        proxy_header.addStretch()
        self._proxy_switch = SwitchButton(proxy_card)
        self._proxy_switch.checkedChanged.connect(self._on_proxy_toggle)
        proxy_header.addWidget(self._proxy_switch)
        proxy_layout.addLayout(proxy_header)

        proxy_layout.addWidget(
            BodyLabel(
                tr(
                    "HTTP/SOCKS proxy (e.g. http://127.0.0.1:7890)",
                    "HTTP/SOCKS 代理（例如 http://127.0.0.1:7890）",
                    "HTTP/SOCKS プロキシ（例: http://127.0.0.1:7890）",
                ),
                proxy_card,
            )
        )

        self._proxy_widget = QWidget(proxy_card)
        proxy_inner = QHBoxLayout(self._proxy_widget)
        proxy_inner.setContentsMargins(0, 0, 0, 0)
        self._proxy_edit = LineEdit(self._proxy_widget)
        self._proxy_edit.setPlaceholderText("http://127.0.0.1:7890")
        self._proxy_edit.textChanged.connect(self._on_proxy_url_changed)

        apply_proxy_btn = PrimaryPushButton(tr("Apply", "应用", "適用"), self._proxy_widget)
        apply_proxy_btn.setFixedWidth(80)
        apply_proxy_btn.clicked.connect(self._apply_proxy)

        proxy_inner.addWidget(self._proxy_edit, stretch=1)
        proxy_inner.addWidget(apply_proxy_btn)
        proxy_layout.addWidget(self._proxy_widget)
        layout.addWidget(proxy_card)

        # ── Aria2 RPC ───────────────────────────────────────────────────────
        aria2_card = CardWidget(self._content)
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
        self._aria2_token_edit.setPlaceholderText(tr("RPC token (optional)", "RPC token（可留空）", "RPC token（任意）"))
        aria2_inner.addWidget(self._aria2_token_edit)

        aria2_layout.addWidget(self._aria2_widget)
        layout.addWidget(aria2_card)

        # ── Save button ───────────────────────────────────────────────────────
        save_btn = PrimaryPushButton(tr("Save All Settings", "保存所有设置", "すべて保存"), self._content, FluentIcon.SAVE)
        save_btn.setFixedWidth(140)
        save_btn.clicked.connect(self._save_settings)
        layout.addWidget(save_btn, alignment=Qt.AlignmentFlag.AlignLeft)

        layout.addStretch()

    # ── Load / save ───────────────────────────────────────────────────────────

    def _load_settings(self):
        self._loading_settings = True
        self._dir_edit.setText(app_config.download_dir)
        self._conc_slider.setValue(app_config.max_concurrent)
        self._conc_input.setText(str(app_config.max_concurrent))
        self._proxy_switch.setChecked(app_config.proxy_enabled)
        self._proxy_edit.setText(app_config.proxy_url)
        self._proxy_widget.setVisible(app_config.proxy_enabled)
        self._aria2_switch.setChecked(app_config.aria2_rpc_enabled)
        self._aria2_url_edit.setText(app_config.aria2_rpc_url)
        self._aria2_token_edit.setText(app_config.aria2_rpc_token)
        self._aria2_widget.setVisible(app_config.aria2_rpc_enabled)
        self._name_tpl_edit.setText(app_config.filename_template)
        self._skip_existing_switch.setChecked(app_config.skip_existing_files)
        self._download_thumb_switch.setChecked(app_config.download_thumbnail)
        self._collect_nfo_switch.setChecked(app_config.collect_nfo_info)
        action = str(app_config.completed_task_click_action or "folder").lower()
        self._completed_click_combo.setCurrentIndex(1 if action == "player" else 0)
        lang = app_config.ui_language.lower()
        if lang.startswith("en"):
            self._lang_combo.setCurrentIndex(1)
        elif lang.startswith("ja") or lang.startswith("jp"):
            self._lang_combo.setCurrentIndex(2)
        else:
            self._lang_combo.setCurrentIndex(0)
        self._search_limit_switch.setChecked(app_config.search_limit_enabled)
        self._search_limit_edit.setText(str(max(1, app_config.search_limit_count)))
        self._search_limit_edit.setEnabled(app_config.search_limit_enabled)

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
        self._loading_settings = False

    # ── Login helpers ─────────────────────────────────────────────────────────

    def _set_logged_in_ui(self, logged_in: bool, status_text: str = ""):
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

        self._login_btn.setEnabled(False)
        self._login_status_lbl.setText(tr("Signing in...", "登录中…", "ログイン中..."))
        signal_bus.log_message.emit(
            tr(
                "[Login] Signing in ...",
                "[登录] 正在登录…",
                "[ログイン] サインイン中...",
            )
        )

        self._worker = LoginWorker(credential, password)
        self._worker.finished.connect(lambda ok, msg: self._on_login_finished(ok, msg, silent))
        self._worker.start()

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

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_auth_toggle(self, checked: bool):
        app_config.auth_enabled = checked
        self._cred_widget.setVisible(checked)
        if not checked:
            download_manager.set_login(False)
            self._set_logged_in_ui(False, "")
            signal_bus.login_state_changed.emit(False)

    def _on_quality_changed(self, idx: int):
        q = ["Source", "540", "360"][idx]
        app_config.preferred_quality = q

    def _on_language_changed(self, idx: int):
        if self._loading_settings:
            return
        if idx == 1:
            app_config.ui_language = "en_US"
        elif idx == 2:
            app_config.ui_language = "ja_JP"
        else:
            app_config.ui_language = "zh_CN"
        signal_bus.language_changed.emit(app_config.ui_language)

    def _browse_dir(self):
        current = self._dir_edit.text() or os.path.expanduser("~")
        chosen = QFileDialog.getExistingDirectory(
            self,
            tr("Choose Download Directory", "选择下载目录", "ダウンロード先を選択"),
            current,
        )
        if chosen:
            self._dir_edit.setText(chosen)
            app_config.download_dir = chosen

    def _on_concurrency_changed(self, value: int):
        self._conc_input.setText(str(value))
        app_config.max_concurrent = value

    def _on_concurrency_input_finished(self):
        text = self._conc_input.text().strip()
        if not text:
            self._conc_input.setText(str(self._conc_slider.value()))
            return
        try:
            value = int(text)
        except ValueError:
            self._conc_input.setText(str(self._conc_slider.value()))
            return
        value = max(1, min(10, value))
        self._conc_slider.setValue(value)
        self._conc_input.setText(str(value))

    def _on_search_limit_toggle(self, checked: bool):
        app_config.search_limit_enabled = checked
        self._search_limit_edit.setEnabled(checked)

    def _on_search_limit_input_finished(self):
        text = self._search_limit_edit.text().strip()
        if not text:
            self._search_limit_edit.setText(str(max(1, app_config.search_limit_count)))
            return
        try:
            value = int(text)
        except ValueError:
            self._search_limit_edit.setText(str(max(1, app_config.search_limit_count)))
            return
        value = max(1, min(5000, value))
        self._search_limit_edit.setText(str(value))
        app_config.search_limit_count = value

    def _on_proxy_toggle(self, checked: bool):
        app_config.proxy_enabled = checked
        self._proxy_widget.setVisible(checked)
        if not checked:
            download_manager.api.set_proxy("")

    def _on_proxy_url_changed(self, text: str):
        app_config.proxy_url = text

    def _on_aria2_toggle(self, checked: bool):
        app_config.aria2_rpc_enabled = checked
        self._aria2_widget.setVisible(checked)

    def _apply_proxy(self):
        if app_config.proxy_enabled:
            download_manager.apply_config()
            InfoBar.success(
                title=tr("Proxy Applied", "代理已应用", "プロキシを適用しました"),
                content=tr("Current proxy: ", "当前代理: ", "現在のプロキシ: ") + app_config.proxy_url,
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )

    def _save_settings(self):
        self._on_concurrency_input_finished()
        app_config.download_dir = self._dir_edit.text()
        app_config.max_concurrent = self._conc_slider.value()
        app_config.proxy_enabled = self._proxy_switch.isChecked()
        app_config.proxy_url = self._proxy_edit.text()
        app_config.aria2_rpc_enabled = self._aria2_switch.isChecked()
        app_config.aria2_rpc_url = self._aria2_url_edit.text().strip()
        app_config.aria2_rpc_token = self._aria2_token_edit.text().strip()
        app_config.filename_template = self._name_tpl_edit.text().strip() or "{username}/{YYYY-MM-DD}_{title}_{id}.mp4"
        app_config.skip_existing_files = self._skip_existing_switch.isChecked()
        app_config.download_thumbnail = self._download_thumb_switch.isChecked()
        app_config.collect_nfo_info = self._collect_nfo_switch.isChecked()
        app_config.completed_task_click_action = (
            "player" if self._completed_click_combo.currentIndex() == 1 else "folder"
        )
        self._on_search_limit_input_finished()
        app_config.search_limit_enabled = self._search_limit_switch.isChecked()
        if app_config.proxy_enabled:
            download_manager.apply_config()
        InfoBar.success(
            title=tr("Settings Saved", "设置已保存", "設定を保存しました"),
            content="",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=2500,
            parent=self,
        )

    def _confirm_clear_temp_files(self):
        box = QMessageBox(self)
        box.setWindowTitle(tr("Confirm Cleanup", "确认清理", "削除確認"))
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(
            tr(
                "All *_temp files in download directory will be deleted (including .aria2 sidecars).",
                "将删除下载目录下所有 *_temp 文件（含对应 .aria2 临时索引）。",
                "ダウンロード先の *_temp ファイル（.aria2 含む）を削除します。",
            )
        )
        box.setInformativeText(
            tr(
                "This action cannot be undone. Continue?",
                "此操作不可撤销，是否继续？",
                "この操作は取り消せません。続行しますか？",
            )
        )
        box.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        box.setDefaultButton(QMessageBox.StandardButton.No)
        if box.exec() != QMessageBox.StandardButton.Yes:
            return

        removed, failed = download_manager.clear_temp_files()
        signal_bus.log_message.emit(
            tr(
                f"[Cleanup] Temp cleanup done, removed {removed}, failed {failed}",
                f"[清理] 临时文件清理完成，删除 {removed} 个，失败 {failed} 个",
                f"[クリーンアップ] 一時ファイル削除完了: 削除 {removed} / 失敗 {failed}",
            )
        )

        if failed:
            InfoBar.warning(
                title=tr("Cleanup finished (partial failure)", "清理完成（部分失败）", "クリーンアップ完了（一部失敗）"),
                content=tr(
                    f"Removed {removed}, failed {failed}",
                    f"已删除 {removed} 个，失败 {failed} 个",
                    f"削除 {removed} 件、失敗 {failed} 件",
                ),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=4000,
                parent=self,
            )
            return

        InfoBar.success(
            title=tr("Cleanup finished", "清理完成", "クリーンアップ完了"),
            content=tr(
                f"Removed {removed} *_temp files",
                f"已删除 {removed} 个 *_temp 文件",
                f"*_temp ファイルを {removed} 件削除しました",
            ),
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=3000,
            parent=self,
        )
