"""Settings Interface — login, quality, download dir, concurrency, proxy."""
from __future__ import annotations

import json
import os

from PySide6.QtCore import QEvent, QMimeData, QPoint, Qt, QThread, Signal
from PySide6.QtGui import QDrag, QIntValidator
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

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
    SpinBox,
    SubtitleLabel,
    SwitchButton,
    TitleLabel,
    ToolButton,
)

from ..config import app_config
from ..core.manager import download_manager
from ..core.download_policy import normalize_hhmm
from ..core.rules import BUILTIN_DEFAULT_RULE_ID, rule_store
from ..i18n import tr
from ..signal_bus import signal_bus
from .ui_state import show_fluent_confirmation
from .worker_lifecycle import stop_qthreads


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


class DraggableSettingsCard(CardWidget):
    """A settings card that can be reordered inside the settings board."""

    def __init__(self, card_key: str, board: "SettingsCardBoard"):
        super().__init__(board)
        self.card_key = str(card_key)
        self._board = board
        self._drag_start_pos = QPoint()
        self._drag_start_global = QPoint()
        self.setProperty("settingsCardKey", self.card_key)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setToolTip(
            tr(
                "Drag this card to reorder settings",
                "拖动此卡片可调整设置顺序",
                "このカードをドラッグして設定順を変更",
            )
        )

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (
            event.buttons() & Qt.MouseButton.LeftButton
            and (event.position().toPoint() - self._drag_start_pos).manhattanLength()
            >= QApplication.startDragDistance()
        ):
            drag = QDrag(self)
            mime_data = QMimeData()
            mime_data.setText(self.card_key)
            drag.setMimeData(mime_data)
            drag.exec(Qt.DropAction.MoveAction)
            return
        super().mouseMoveEvent(event)

    def enable_drag_sources(self):
        # Inputs keep their normal mouse behavior.  Text labels provide a
        # reliable drag surface even when a compact card has no empty padding.
        for child in self.findChildren(QWidget):
            if isinstance(child, (BodyLabel, SubtitleLabel, TitleLabel)):
                child.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_global = event.globalPosition().toPoint()
        elif (
            event.type() == QEvent.Type.MouseMove
            and event.buttons() & Qt.MouseButton.LeftButton
            and (event.globalPosition().toPoint() - self._drag_start_global).manhattanLength()
            >= QApplication.startDragDistance()
        ):
            drag = QDrag(self)
            mime_data = QMimeData()
            mime_data.setText(self.card_key)
            drag.setMimeData(mime_data)
            drag.exec(Qt.DropAction.MoveAction)
            return True
        return super().eventFilter(watched, event)


class SettingsCardBoard(QWidget):
    """Responsive two-column board with persisted drag ordering."""

    _ORDER_KEY = "settings_card_order_v1"
    _DEFAULT_ORDER = (
        "account",
        "download_dir",
        "quality",
        "concurrency",
        "cover_performance",
        "subscription_automation",
        "download_policy",
        "updates",
        "search_bridge",
        "behavior",
        "proxy",
        "search_limit",
        "subscription_prompt",
        "language",
        "data_paths",
        "aria2",
    )

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("settingsCardBoard")
        self.setAcceptDrops(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(16)
        self._grid.setVerticalSpacing(16)
        self._cards: dict[str, DraggableSettingsCard] = {}
        self._saved_order = self._read_saved_order()

    @staticmethod
    def _read_saved_order() -> list[str]:
        raw = app_config.get_ui_value(SettingsCardBoard._ORDER_KEY, "")
        try:
            value = json.loads(str(raw or ""))
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    def create_card(self, card_key: str) -> DraggableSettingsCard:
        return DraggableSettingsCard(card_key, self)

    def add_card(self, card_key: str, card: DraggableSettingsCard):
        card_key = str(card_key).strip()
        if not card_key:
            return
        self._cards[card_key] = card
        card.setParent(self)
        card.enable_drag_sources()
        self._reflow()

    def _ordered_keys(self) -> list[str]:
        known = set(self._cards)
        order: list[str] = []
        for key in (*self._saved_order, *self._DEFAULT_ORDER, *self._cards.keys()):
            if key in known and key not in order:
                order.append(key)
        return order

    def _column_count(self) -> int:
        width = max(0, self.width())
        if width >= 760:
            return 2
        return 1

    def _reflow(self):
        while self._grid.count():
            item = self._grid.takeAt(0)
            if item.widget():
                item.widget().show()
        columns = self._column_count()
        for column in range(2):
            self._grid.setColumnStretch(column, 1 if column < columns else 0)
        for index, card_key in enumerate(self._ordered_keys()):
            card = self._cards[card_key]
            row, column = divmod(index, columns)
            self._grid.addWidget(card, row, column)
        self.setMinimumHeight(self._grid.sizeHint().height())
        self.updateGeometry()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow()

    def _card_from_position(self, position: QPoint) -> DraggableSettingsCard | None:
        widget = self.childAt(position)
        while widget is not None and widget is not self:
            if isinstance(widget, DraggableSettingsCard):
                return widget
            widget = widget.parentWidget()
        return None

    def _persist_order(self, order: list[str]):
        self._saved_order = list(order)
        app_config.set_ui_value(self._ORDER_KEY, json.dumps(order, ensure_ascii=False))

    def dropEvent(self, event):
        source_key = str(event.mimeData().text() or "").strip()
        order = self._ordered_keys()
        if source_key not in order:
            event.ignore()
            return
        target = self._card_from_position(event.position().toPoint())
        target_key = target.card_key if target else ""
        order.remove(source_key)
        if target_key and target_key != source_key:
            target_index = order.index(target_key)
            if event.position().toPoint().y() > target.geometry().center().y():
                target_index += 1
            order.insert(target_index, source_key)
        else:
            order.append(source_key)
        self._persist_order(order)
        self._reflow()
        event.acceptProposedAction()

    def dragEnterEvent(self, event):
        if event.mimeData().hasText() and event.mimeData().text() in self._cards:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)


# ── Settings Interface ────────────────────────────────────────────────────────

class SettingsInterface(ScrollArea):
    """Page for configuring application-level settings (including login)."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("SettingsInterface")
        # A native QScrollArea viewport otherwise keeps its light palette and
        # paints an opaque white page over FluentWindow's Mica/dark background.
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet(
            "QScrollArea#SettingsInterface { background: transparent; border: none; }"
            "QScrollArea#SettingsInterface > QWidget > QWidget { background: transparent; }"
            "QWidget#settingsContent { background: transparent; }"
        )
        self.viewport().setAutoFillBackground(False)

        self._worker: LoginWorker | None = None
        self._loading_settings = False

        self._content = QWidget(self)
        self._content.setObjectName("settingsContent")
        self._content.setAutoFillBackground(False)
        self.setWidget(self._content)
        self.setWidgetResizable(True)

        self._build_ui()
        self._load_settings()
        signal_bus.rules_changed.connect(self._reload_auto_enqueue_rules)

        # Startup auth: prefer cached token for faster boot; fallback to credential login.
        if download_manager.restore_cached_login():
            self._set_logged_in_ui(True, tr("✓ Signed in (cached token)", "✓ 已登录（已加载本地 Token）", "✓ ログイン済み（ローカルトークン使用）"))
            signal_bus.login_state_changed.emit(True)
        elif app_config.auth_enabled and app_config.username and app_config.password:
            self._do_login(silent=True)

    def shutdown(self, *, timeout_ms: int = 30_000) -> bool:
        """Wait for an in-flight login request before destroying its QThread."""
        return stop_qthreads([self._worker], timeout_ms=timeout_ms)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self._content)
        layout.setContentsMargins(36, 24, 36, 24)
        layout.setSpacing(16)

        layout.addWidget(TitleLabel(tr("Settings", "应用设置", "設定"), self._content))
        self._settings_board = SettingsCardBoard(self._content)
        layout.addWidget(self._settings_board)

        # ── Language ─────────────────────────────────────────────────────────
        lang_card = self._settings_board.create_card("language")
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

        self._settings_board.add_card("language", lang_card)

        # ── Data location (portable mode) ───────────────────────────────────
        data_card = self._settings_board.create_card("data_paths")
        data_layout = QVBoxLayout(data_card)
        data_layout.setContentsMargins(20, 16, 20, 16)
        data_layout.setSpacing(8)
        data_layout.addWidget(SubtitleLabel(tr("Local Data Paths (Portable)", "本地数据位置（绿色模式）", "ローカルデータパス（ポータブル）"), data_card))
        data_layout.addWidget(BodyLabel(f"{tr('Data dir', '数据目录', 'データディレクトリ')}: {app_config.app_data_dir}", data_card))
        data_layout.addWidget(BodyLabel(f"{tr('Config file', '配置文件', '設定ファイル')}: {app_config.config_path}", data_card))
        data_layout.addWidget(BodyLabel(f"{tr('History DB', '下载历史库', '履歴DB')}: {app_config.history_db_path}", data_card))
        self._settings_board.add_card("data_paths", data_card)

        # ── Account / Login card ──────────────────────────────────────────────
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
        self._settings_board.add_card("account", login_card)

        # ── Quality preference card ───────────────────────────────────────────
        quality_card = self._settings_board.create_card("quality")
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
        self._quality_combo.addItems([tr("Source", "原画", "オリジナル"), "540p", "360p"])
        self._quality_combo.setFixedWidth(180)
        self._quality_combo.currentIndexChanged.connect(self._on_quality_changed)
        quality_row.addWidget(self._quality_combo)
        quality_row.addStretch()
        quality_layout.addLayout(quality_row)
        self._settings_board.add_card("quality", quality_card)

        # ── Download directory ────────────────────────────────────────────────
        dir_card = self._settings_board.create_card("download_dir")
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
        self._settings_board.add_card("download_dir", dir_card)

        # ── Global download behavior ─────────────────────────────────────────
        name_card = self._settings_board.create_card("behavior")
        name_layout = QVBoxLayout(name_card)
        name_layout.setContentsMargins(20, 16, 20, 16)
        name_layout.setSpacing(10)
        name_layout.addWidget(SubtitleLabel(tr("Download Behavior", "下载行为", "ダウンロード動作"), name_card))

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

        self._settings_board.add_card("behavior", name_card)

        # ── Concurrency ───────────────────────────────────────────────────────
        conc_card = self._settings_board.create_card("concurrency")
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

        stall_row = QHBoxLayout()
        stall_row.addWidget(
            BodyLabel(
                tr(
                    "Auto-cancel idle task after seconds (0 disables)",
                    "无响应自动中断秒数（0 关闭）",
                    "無応答の自動中断秒数（0 で無効）",
                ),
                conc_card,
            )
        )
        stall_row.addStretch()
        self._stall_timeout_edit = LineEdit(conc_card)
        self._stall_timeout_edit.setFixedWidth(100)
        self._stall_timeout_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._stall_timeout_edit.setValidator(QIntValidator(0, 3600, self._stall_timeout_edit))
        self._stall_timeout_edit.setPlaceholderText("0-3600")
        self._stall_timeout_edit.editingFinished.connect(self._on_stall_timeout_input_finished)
        stall_row.addWidget(self._stall_timeout_edit)
        conc_layout.addLayout(stall_row)

        auto_restore_row = QHBoxLayout()
        auto_restore_row.addWidget(
            BodyLabel(
                tr(
                    "Auto-restore idle-cancelled tasks when the queue becomes idle",
                    "任务空闲后自动恢复超时中断项",
                    "待機状態になったら自動中断タスクを復元",
                ),
                conc_card,
            )
        )
        auto_restore_row.addStretch()
        self._auto_restore_stalled_switch = SwitchButton(conc_card)
        self._auto_restore_stalled_switch.setToolTip(
            tr(
                "Only restores tasks cancelled by the idle watchdog; manual Cancel All is not restored.",
                "只恢复无响应检测自动中断的任务；人为点击全部中断不会自动恢复。",
                "無応答ウォッチドッグで中断されたタスクのみ復元します。手動の全件中断は復元しません。",
            )
        )
        self._auto_restore_stalled_switch.checkedChanged.connect(self._on_auto_restore_stalled_toggle)
        auto_restore_row.addWidget(self._auto_restore_stalled_switch)
        conc_layout.addLayout(auto_restore_row)
        self._settings_board.add_card("concurrency", conc_card)

        # ── Background refresh and cover performance ─────────────────────────
        cover_card = self._settings_board.create_card("cover_performance")
        cover_layout = QVBoxLayout(cover_card)
        cover_layout.setContentsMargins(20, 16, 20, 16)
        cover_layout.setSpacing(10)
        cover_layout.addWidget(
            SubtitleLabel(
                tr(
                    "Refresh and Cover Performance",
                    "刷新与封面性能",
                    "更新とカバーのパフォーマンス",
                ),
                cover_card,
            )
        )
        cover_layout.addWidget(
            BodyLabel(
                tr(
                    "Controls image downloads for search/subscriptions and incremental account-feed refresh.",
                    "控制搜索/订阅封面并发，并让账户订阅刷新只检查已知视频之前的新内容。",
                    "検索・購読カバーの同時数と、既知の動画までを確認する増分更新を設定します。",
                ),
                cover_card,
            )
        )

        cover_workers_row = QHBoxLayout()
        cover_workers_row.addWidget(
            BodyLabel(tr("Cover download concurrency", "封面获取并发数", "カバー取得の同時数"), cover_card)
        )
        self._cover_download_workers_spin = SpinBox(cover_card)
        self._cover_download_workers_spin.setRange(1, 16)
        self._cover_download_workers_spin.setFixedWidth(132)
        self._cover_download_workers_spin.valueChanged.connect(self._on_cover_download_workers_changed)
        cover_workers_row.addWidget(self._cover_download_workers_spin)
        cover_workers_row.addStretch()
        cover_layout.addLayout(cover_workers_row)

        refresh_workers_row = QHBoxLayout()
        refresh_workers_row.addWidget(
            BodyLabel(tr("Subscription refresh concurrency", "订阅刷新并发数", "購読更新の同時数"), cover_card)
        )
        self._subscription_refresh_workers_spin = SpinBox(cover_card)
        self._subscription_refresh_workers_spin.setRange(1, 8)
        self._subscription_refresh_workers_spin.setFixedWidth(132)
        self._subscription_refresh_workers_spin.valueChanged.connect(self._on_subscription_refresh_workers_changed)
        refresh_workers_row.addWidget(self._subscription_refresh_workers_spin)
        refresh_workers_row.addStretch()
        cover_layout.addLayout(refresh_workers_row)

        incremental_row = QHBoxLayout()
        incremental_row.addWidget(
            BodyLabel(
                tr(
                    "Incremental account subscription refresh",
                    "账户订阅增量刷新",
                    "アカウント購読を増分更新",
                ),
                cover_card,
            )
        )
        incremental_row.addStretch()
        self._subscription_incremental_switch = SwitchButton(cover_card)
        self._subscription_incremental_switch.checkedChanged.connect(
            self._on_subscription_incremental_toggle
        )
        incremental_row.addWidget(self._subscription_incremental_switch)
        cover_layout.addLayout(incremental_row)
        self._settings_board.add_card("cover_performance", cover_card)

        # ── Search download limit ───────────────────────────────────────────
        search_card = self._settings_board.create_card("search_limit")
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
        self._settings_board.add_card("search_limit", search_card)

        # ── Search bridge resolution ───────────────────────────────────────
        search_resolve_card = self._settings_board.create_card("search_bridge")
        search_resolve_layout = QVBoxLayout(search_resolve_card)
        search_resolve_layout.setContentsMargins(20, 16, 20, 16)
        search_resolve_layout.setSpacing(10)
        search_resolve_layout.addWidget(
            SubtitleLabel(
                tr(
                    "Oreno3D / Iwara Search Loading",
                    "Oreno3D / Iwara 搜索加载",
                    "Oreno3D / Iwara 検索読み込み",
                ),
                search_resolve_card,
            )
        )
        search_resolve_layout.addWidget(
            BodyLabel(
                tr(
                    "Oreno3D supplies the thumbnail and Iwara ID bridge. Metadata is always read from Iwara; choose when the bridge should be resolved.",
                    "Oreno3D 只提供缩略图和 Iwara ID 跳板；标题、作者、标签、评论等元数据始终从 Iwara 读取，可选择解析时机。",
                    "Oreno3D はサムネイルと Iwara ID への橋渡しだけを行い、メタデータは常に Iwara から取得します。解決タイミングを選べます。",
                ),
                search_resolve_card,
            )
        )

        resolve_mode_row = QHBoxLayout()
        resolve_mode_row.addWidget(
            BodyLabel(
                tr("Resolve timing", "解析时机", "解決タイミング"),
                search_resolve_card,
            )
        )
        self._search_resolution_mode_combo = ComboBox(search_resolve_card)
        self._search_resolution_mode_combo.addItem(
            tr("Background pre-resolve", "后台预解析", "バックグラウンドで事前解決")
        )
        self._search_resolution_mode_combo.setItemData(0, "eager")
        self._search_resolution_mode_combo.addItem(
            tr("On click or download", "点击或下载时解析", "クリックまたはダウンロード時に解決")
        )
        self._search_resolution_mode_combo.setItemData(1, "on_demand")
        self._search_resolution_mode_combo.setFixedWidth(260)
        self._search_resolution_mode_combo.currentIndexChanged.connect(
            self._on_search_resolution_mode_changed
        )
        resolve_mode_row.addWidget(self._search_resolution_mode_combo)
        resolve_mode_row.addStretch()
        search_resolve_layout.addLayout(resolve_mode_row)

        resolve_workers_row = QHBoxLayout()
        resolve_workers_row.addWidget(
            BodyLabel(
                tr("Oreno3D ID concurrency", "Oreno3D ID 并发数", "Oreno3D ID 同時実行数"),
                search_resolve_card,
            )
        )
        self._search_resolution_workers_spin = SpinBox(search_resolve_card)
        self._search_resolution_workers_spin.setRange(1, 8)
        self._search_resolution_workers_spin.setFixedWidth(132)
        self._search_resolution_workers_spin.valueChanged.connect(
            self._on_search_resolution_workers_changed
        )
        resolve_workers_row.addWidget(self._search_resolution_workers_spin)
        resolve_workers_row.addWidget(
            BodyLabel(
                tr(
                    "Independent Oreno3D detail requests; Iwara metadata remains rate-limited by its API session.",
                    "使用独立 Oreno3D 详情请求；Iwara 元数据仍由 API 会话统一限速。",
                    "Oreno3D 詳細リクエストは独立実行し、Iwara メタデータは API セッション側で制御します。",
                ),
                search_resolve_card,
            )
        )
        resolve_workers_row.addStretch()
        search_resolve_layout.addLayout(resolve_workers_row)
        self._settings_board.add_card("search_bridge", search_resolve_card)

        # ── Subscription prompt behavior ───────────────────────────────────
        sub_prompt_card = self._settings_board.create_card("subscription_prompt")
        sub_prompt_layout = QVBoxLayout(sub_prompt_card)
        sub_prompt_layout.setContentsMargins(20, 16, 20, 16)
        sub_prompt_layout.setSpacing(10)
        sub_prompt_layout.addWidget(
            SubtitleLabel(tr("Download Subscription Prompt", "下载订阅提示", "ダウンロード時の購読確認"), sub_prompt_card)
        )
        sub_prompt_layout.addWidget(
            BodyLabel(
                tr(
                    "When a download input is an author or playlist URL, decide whether it should also be added to local subscriptions.",
                    "当下载输入为作者或播放列表链接时，决定是否同时加入本地订阅列表。",
                    "入力が作者またはプレイリストURLの場合、ローカル購読へ追加するかを決めます。",
                ),
                sub_prompt_card,
            )
        )
        sub_prompt_row = QHBoxLayout()
        self._subscription_prompt_combo = ComboBox(sub_prompt_card)
        self._subscription_prompt_combo.addItems(
            [
                tr("Ask Every Time", "每次询问", "毎回確認"),
                tr("Always Add", "自动加入", "常に追加"),
                tr("Never Ask", "不再提醒", "確認しない"),
            ]
        )
        self._subscription_prompt_combo.setFixedWidth(180)
        sub_prompt_row.addWidget(self._subscription_prompt_combo)
        sub_prompt_row.addStretch()
        sub_prompt_layout.addLayout(sub_prompt_row)
        self._settings_board.add_card("subscription_prompt", sub_prompt_card)

        # ── Proxy ─────────────────────────────────────────────────────────────
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

        # ── Aria2 RPC ───────────────────────────────────────────────────────
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
        self._aria2_token_edit.setPlaceholderText(tr("RPC token (optional)", "RPC token（可留空）", "RPC token（任意）"))
        aria2_inner.addWidget(self._aria2_token_edit)

        aria2_layout.addWidget(self._aria2_widget)
        self._settings_board.add_card("aria2", aria2_card)

        # ── Subscription automation ─────────────────────────────────────────
        automation_card = self._settings_board.create_card("subscription_automation")
        automation_layout = QVBoxLayout(automation_card)
        automation_layout.setContentsMargins(20, 16, 20, 16)
        automation_layout.setSpacing(10)

        refresh_header = QHBoxLayout()
        refresh_header.addWidget(
            SubtitleLabel(
                tr("Subscription Automation", "订阅自动化", "購読自動化"),
                automation_card,
            )
        )
        refresh_header.addStretch()
        self._auto_refresh_switch = SwitchButton(automation_card)
        refresh_header.addWidget(self._auto_refresh_switch)
        automation_layout.addLayout(refresh_header)

        refresh_interval_row = QHBoxLayout()
        refresh_interval_row.addWidget(
            BodyLabel(tr("Refresh interval", "刷新间隔", "更新間隔"), automation_card)
        )
        self._auto_refresh_interval_spin = SpinBox(automation_card)
        self._auto_refresh_interval_spin.setRange(1, 1440)
        self._auto_refresh_interval_spin.setSuffix(
            tr(" min", " 分钟", " 分")
        )
        self._auto_refresh_interval_spin.setFixedWidth(140)
        refresh_interval_row.addWidget(self._auto_refresh_interval_spin)
        refresh_interval_row.addStretch()
        automation_layout.addLayout(refresh_interval_row)

        notification_row = QHBoxLayout()
        notification_row.addWidget(
            BodyLabel(tr("Desktop notifications", "桌面通知", "デスクトップ通知"), automation_card)
        )
        notification_row.addStretch()
        self._desktop_notification_switch = SwitchButton(automation_card)
        notification_row.addWidget(self._desktop_notification_switch)
        automation_layout.addLayout(notification_row)

        enqueue_row = QHBoxLayout()
        enqueue_row.addWidget(
            BodyLabel(tr("Auto queue rule matches", "命中规则后自动入队", "ルール一致を自動追加"), automation_card)
        )
        enqueue_row.addStretch()
        self._auto_enqueue_switch = SwitchButton(automation_card)
        enqueue_row.addWidget(self._auto_enqueue_switch)
        automation_layout.addLayout(enqueue_row)

        rule_row = QHBoxLayout()
        rule_row.addWidget(BodyLabel(tr("Matching rule", "匹配规则", "照合ルール"), automation_card))
        self._auto_enqueue_rule_combo = ComboBox(automation_card)
        self._auto_enqueue_rule_combo.setFixedWidth(220)
        rule_row.addWidget(self._auto_enqueue_rule_combo)
        rule_row.addStretch()
        automation_layout.addLayout(rule_row)

        refresh_now_btn = PrimaryPushButton(
            tr("Refresh Now", "立即刷新", "今すぐ更新"),
            automation_card,
            FluentIcon.SYNC,
        )
        refresh_now_btn.clicked.connect(self._refresh_subscriptions_now)
        automation_layout.addWidget(refresh_now_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        self._settings_board.add_card("subscription_automation", automation_card)

        # ── Runtime download policy ──────────────────────────────────────────
        policy_card = self._settings_board.create_card("download_policy")
        policy_layout = QVBoxLayout(policy_card)
        policy_layout.setContentsMargins(20, 16, 20, 16)
        policy_layout.setSpacing(10)
        policy_layout.addWidget(
            SubtitleLabel(tr("Download Policy", "下载策略", "ダウンロード方針"), policy_card)
        )

        speed_row = QHBoxLayout()
        speed_row.addWidget(BodyLabel(tr("Global speed limit", "全局限速", "全体速度制限"), policy_card))
        self._speed_limit_spin = SpinBox(policy_card)
        self._speed_limit_spin.setRange(1, 10 * 1024 * 1024)
        self._speed_limit_spin.setSuffix(" KiB/s")
        self._speed_limit_spin.setFixedWidth(170)
        speed_row.addWidget(self._speed_limit_spin)
        speed_row.addStretch()
        self._speed_limit_switch = SwitchButton(policy_card)
        speed_row.addWidget(self._speed_limit_switch)
        policy_layout.addLayout(speed_row)

        schedule_row = QHBoxLayout()
        schedule_row.addWidget(BodyLabel(tr("Scheduled starts", "分时下载", "時間帯ダウンロード"), policy_card))
        self._schedule_start_edit = LineEdit(policy_card)
        self._schedule_start_edit.setPlaceholderText("00:00")
        self._schedule_start_edit.setFixedWidth(76)
        schedule_row.addWidget(self._schedule_start_edit)
        schedule_row.addWidget(BodyLabel("—", policy_card))
        self._schedule_end_edit = LineEdit(policy_card)
        self._schedule_end_edit.setPlaceholderText("00:00")
        self._schedule_end_edit.setFixedWidth(76)
        schedule_row.addWidget(self._schedule_end_edit)
        schedule_row.addStretch()
        self._schedule_switch = SwitchButton(policy_card)
        schedule_row.addWidget(self._schedule_switch)
        policy_layout.addLayout(schedule_row)
        policy_layout.addWidget(
            BodyLabel(
                tr(
                    "Only new tasks start inside the window; active transfers finish normally. Equal times mean all day.",
                    "仅在时间窗内启动新任务；已开始的传输会正常完成。起止相同表示全天。",
                    "時間帯内だけ新規開始し、実行中の転送は完了まで継続します。同時刻は終日です。",
                ),
                policy_card,
            )
        )
        self._settings_board.add_card("download_policy", policy_card)

        # ── GitHub Release updates ───────────────────────────────────────────
        update_card = self._settings_board.create_card("updates")
        update_layout = QVBoxLayout(update_card)
        update_layout.setContentsMargins(20, 16, 20, 16)
        update_layout.setSpacing(10)
        update_header = QHBoxLayout()
        update_header.addWidget(
            SubtitleLabel(tr("GitHub Release Updates", "GitHub Release 更新", "GitHub Release 更新"), update_card)
        )
        update_header.addStretch()
        self._update_check_switch = SwitchButton(update_card)
        update_header.addWidget(self._update_check_switch)
        update_layout.addLayout(update_header)
        update_layout.addWidget(
            BodyLabel(
                tr(
                    "Check Moeary/IwaraTool Releases at startup and open the release page on confirmation.",
                    "启动后检查 Moeary/IwaraTool Releases，确认后打开 Release 页面。",
                    "起動後に Moeary/IwaraTool Releases を確認し、承認後にページを開きます。",
                ),
                update_card,
            )
        )
        check_update_btn = PrimaryPushButton(
            tr("Check Now", "立即检查", "今すぐ確認"),
            update_card,
            FluentIcon.UPDATE,
        )
        check_update_btn.clicked.connect(self._check_updates_now)
        update_layout.addWidget(check_update_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        self._settings_board.add_card("updates", update_card)

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
        self._stall_timeout_edit.setText(str(app_config.task_stall_timeout_seconds))
        self._auto_restore_stalled_switch.setChecked(app_config.auto_restore_stalled_cancelled)
        self._api_proxy_switch.setChecked(app_config.api_proxy_enabled)
        self._api_proxy_edit.setText(app_config.api_proxy_url)
        self._api_proxy_widget.setVisible(app_config.api_proxy_enabled)
        self._download_proxy_switch.setChecked(app_config.download_proxy_enabled)
        self._download_proxy_edit.setText(app_config.download_proxy_url)
        self._download_proxy_widget.setVisible(app_config.download_proxy_enabled)
        self._aria2_switch.setChecked(app_config.aria2_rpc_enabled)
        self._aria2_url_edit.setText(app_config.aria2_rpc_url)
        self._aria2_token_edit.setText(app_config.aria2_rpc_token)
        self._aria2_widget.setVisible(app_config.aria2_rpc_enabled)
        self._auto_refresh_switch.setChecked(app_config.subscription_auto_refresh_enabled)
        self._auto_refresh_interval_spin.setValue(app_config.subscription_refresh_interval_minutes)
        self._desktop_notification_switch.setChecked(app_config.desktop_notifications_enabled)
        self._auto_enqueue_switch.setChecked(app_config.subscription_auto_enqueue_enabled)
        self._reload_auto_enqueue_rules(app_config.subscription_auto_enqueue_rule_id)
        self._speed_limit_switch.setChecked(app_config.global_speed_limit_enabled)
        self._speed_limit_spin.setValue(max(1, app_config.global_speed_limit_kib or 1024))
        self._schedule_switch.setChecked(app_config.download_schedule_enabled)
        self._schedule_start_edit.setText(normalize_hhmm(app_config.download_schedule_start))
        self._schedule_end_edit.setText(normalize_hhmm(app_config.download_schedule_end))
        self._update_check_switch.setChecked(app_config.update_check_enabled)
        self._skip_existing_switch.setChecked(app_config.skip_existing_files)
        action = str(app_config.completed_task_click_action or "folder").lower()
        self._completed_click_combo.setCurrentIndex(1 if action == "player" else 0)
        prompt_mode = app_config.subscription_prompt_mode
        self._subscription_prompt_combo.setCurrentIndex({"ask": 0, "always": 1, "never": 2}.get(prompt_mode, 0))
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
        search_resolution_mode = str(
            app_config.get_ui_value("search_iwara_resolution_mode_v1", "eager")
            or "eager"
        ).strip().lower()
        self._search_resolution_mode_combo.setCurrentIndex(
            1 if search_resolution_mode == "on_demand" else 0
        )
        try:
            search_resolution_workers = int(
                app_config.get_ui_value("search_iwara_resolution_workers_v1", 4) or 4
            )
        except (TypeError, ValueError):
            search_resolution_workers = 4
        self._search_resolution_workers_spin.setValue(
            max(1, min(8, search_resolution_workers))
        )
        try:
            cover_download_workers = int(
                app_config.get_ui_value("cover_download_workers_v1", 6) or 6
            )
        except (TypeError, ValueError):
            cover_download_workers = 6
        self._cover_download_workers_spin.setValue(max(1, min(16, cover_download_workers)))
        try:
            subscription_refresh_workers = int(
                app_config.get_ui_value("subscription_refresh_workers_v1", 3) or 3
            )
        except (TypeError, ValueError):
            subscription_refresh_workers = 3
        self._subscription_refresh_workers_spin.setValue(
            max(1, min(8, subscription_refresh_workers))
        )
        incremental_value = app_config.get_ui_value(
            "subscription_incremental_refresh_v1", True
        )
        if isinstance(incremental_value, str):
            incremental_enabled = incremental_value.strip().casefold() not in {
                "0",
                "false",
                "off",
                "no",
            }
        else:
            incremental_enabled = bool(incremental_value)
        self._subscription_incremental_switch.setChecked(incremental_enabled)

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

    def _on_stall_timeout_input_finished(self):
        text = self._stall_timeout_edit.text().strip()
        if not text:
            value = 30
        else:
            try:
                value = int(text)
            except ValueError:
                value = app_config.task_stall_timeout_seconds
        value = max(0, min(3600, value))
        self._stall_timeout_edit.setText(str(value))
        app_config.task_stall_timeout_seconds = value

    def _on_auto_restore_stalled_toggle(self, checked: bool):
        if self._loading_settings:
            return
        app_config.auto_restore_stalled_cancelled = bool(checked)

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

    def _on_search_resolution_mode_changed(self, _index: int):
        if self._loading_settings:
            return
        mode = str(self._search_resolution_mode_combo.currentData() or "eager")
        if mode not in {"eager", "on_demand"}:
            mode = "eager"
        app_config.set_ui_value("search_iwara_resolution_mode_v1", mode)

    def _on_search_resolution_workers_changed(self, value: int):
        if self._loading_settings:
            return
        value = max(1, min(8, int(value)))
        app_config.set_ui_value("search_iwara_resolution_workers_v1", value)

    def _on_cover_download_workers_changed(self, value: int):
        if self._loading_settings:
            return
        value = max(1, min(16, int(value)))
        app_config.set_ui_value("cover_download_workers_v1", value)

    def _on_subscription_refresh_workers_changed(self, value: int):
        if self._loading_settings:
            return
        value = max(1, min(8, int(value)))
        app_config.set_ui_value("subscription_refresh_workers_v1", value)

    def _on_subscription_incremental_toggle(self, checked: bool):
        if self._loading_settings:
            return
        app_config.set_ui_value("subscription_incremental_refresh_v1", bool(checked))

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

    def _reload_auto_enqueue_rules(self, selected_rule_id: str = ""):
        if not hasattr(self, "_auto_enqueue_rule_combo"):
            return
        selected = str(
            selected_rule_id
            or self._auto_enqueue_rule_combo.currentData()
            or app_config.subscription_auto_enqueue_rule_id
            or BUILTIN_DEFAULT_RULE_ID
        )
        self._auto_enqueue_rule_combo.blockSignals(True)
        self._auto_enqueue_rule_combo.clear()
        target_index = 0
        for index, rule in enumerate(rule_store.list_available()):
            self._auto_enqueue_rule_combo.addItem(str(rule.get("name", "") or ""))
            self._auto_enqueue_rule_combo.setItemData(index, str(rule.get("id", "") or ""))
            if str(rule.get("id", "") or "") == selected:
                target_index = index
        self._auto_enqueue_rule_combo.setCurrentIndex(target_index)
        self._auto_enqueue_rule_combo.blockSignals(False)

    def _refresh_subscriptions_now(self):
        self._save_background_settings()
        from ..core.background_services import background_service

        background_service.refresh_subscriptions_now()
        InfoBar.info(
            title=tr("Refresh queued", "刷新已提交", "更新を受け付けました"),
            content="",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=2000,
            parent=self,
        )

    def _check_updates_now(self):
        self._save_background_settings()
        from ..core.background_services import background_service

        background_service.check_updates_now()

    def _save_background_settings(self):
        app_config.subscription_auto_refresh_enabled = self._auto_refresh_switch.isChecked()
        app_config.subscription_refresh_interval_minutes = self._auto_refresh_interval_spin.value()
        app_config.desktop_notifications_enabled = self._desktop_notification_switch.isChecked()
        app_config.subscription_auto_enqueue_enabled = self._auto_enqueue_switch.isChecked()
        app_config.subscription_auto_enqueue_rule_id = str(
            self._auto_enqueue_rule_combo.currentData() or BUILTIN_DEFAULT_RULE_ID
        )
        app_config.global_speed_limit_enabled = self._speed_limit_switch.isChecked()
        app_config.global_speed_limit_kib = self._speed_limit_spin.value()
        app_config.download_schedule_enabled = self._schedule_switch.isChecked()
        start = normalize_hhmm(self._schedule_start_edit.text())
        end = normalize_hhmm(self._schedule_end_edit.text())
        self._schedule_start_edit.setText(start)
        self._schedule_end_edit.setText(end)
        app_config.download_schedule_start = start
        app_config.download_schedule_end = end
        app_config.update_check_enabled = self._update_check_switch.isChecked()
        download_manager.resume_scheduled_downloads()
        signal_bus.background_settings_changed.emit()

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

    def _save_settings(self):
        self._on_concurrency_input_finished()
        self._on_stall_timeout_input_finished()
        app_config.auto_restore_stalled_cancelled = self._auto_restore_stalled_switch.isChecked()
        app_config.download_dir = self._dir_edit.text()
        app_config.max_concurrent = self._conc_slider.value()
        app_config.api_proxy_enabled = self._api_proxy_switch.isChecked()
        app_config.api_proxy_url = self._api_proxy_edit.text().strip() or "http://127.0.0.1:7890"
        app_config.download_proxy_enabled = self._download_proxy_switch.isChecked()
        app_config.download_proxy_url = self._download_proxy_edit.text().strip() or "http://127.0.0.1:7890"
        app_config.aria2_rpc_enabled = self._aria2_switch.isChecked()
        app_config.aria2_rpc_url = self._aria2_url_edit.text().strip()
        app_config.aria2_rpc_token = self._aria2_token_edit.text().strip()
        app_config.skip_existing_files = self._skip_existing_switch.isChecked()
        app_config.completed_task_click_action = (
            "player" if self._completed_click_combo.currentIndex() == 1 else "folder"
        )
        app_config.subscription_prompt_mode = ["ask", "always", "never"][self._subscription_prompt_combo.currentIndex()]
        self._on_search_limit_input_finished()
        app_config.search_limit_enabled = self._search_limit_switch.isChecked()
        self._on_search_resolution_mode_changed(
            self._search_resolution_mode_combo.currentIndex()
        )
        self._on_search_resolution_workers_changed(
            self._search_resolution_workers_spin.value()
        )
        self._on_cover_download_workers_changed(self._cover_download_workers_spin.value())
        self._on_subscription_refresh_workers_changed(
            self._subscription_refresh_workers_spin.value()
        )
        self._on_subscription_incremental_toggle(self._subscription_incremental_switch.isChecked())
        self._save_background_settings()
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
        if not show_fluent_confirmation(
            self,
            tr("Confirm Cleanup", "确认清理", "削除確認"),
            tr(
                "All *_temp files in download directory will be deleted (including .aria2 sidecars).",
                "将删除下载目录下所有 *_temp 文件（含对应 .aria2 临时索引）。",
                "ダウンロード先の *_temp ファイル（.aria2 含む）を削除します。",
            ),
            informative=tr(
                "This action cannot be undone. Continue?",
                "此操作不可撤销，是否继续？",
                "この操作は取り消せません。続行しますか？",
            ),
            yes_text=tr("Delete temp files", "删除临时文件", "一時ファイルを削除"),
            no_text=tr("Cancel", "取消", "キャンセル"),
        ):
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
