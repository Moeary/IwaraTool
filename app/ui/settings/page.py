"""Settings Interface — login, quality, download dir, concurrency, proxy."""
from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QWidget

from qfluentwidgets import FluentIcon, PrimaryPushButton, TitleLabel

from ...config import app_config
from ...core.manager import download_manager
from ...i18n import tr
from ...signal_bus import signal_bus
from .home_cards import HomeSettingsCards
from .sections import SettingsSections
from .shortcuts_cards import ShortcutSettingsCards
from .system_cards import SystemSettingsCards
from ..tag_dictionary_worker import TagDictionaryUpdateWorker
from ..theme import PAGE_MARGINS
from ..worker_lifecycle import stop_qthreads
from .account import LoginWorker, SettingsAccountMixin
from .download_cards import SettingsDownloadCardsMixin
from .general_cards import SettingsGeneralCardsMixin
from .network_cards import SettingsNetworkCardsMixin
from .persistence import SettingsPersistenceMixin
from .search_cards import SettingsSearchCardsMixin
from .subscription_cards import SettingsSubscriptionCardsMixin


class SettingsInterface(
    SettingsAccountMixin,
    SettingsGeneralCardsMixin,
    SettingsDownloadCardsMixin,
    SettingsSearchCardsMixin,
    SettingsSubscriptionCardsMixin,
    SettingsNetworkCardsMixin,
    SettingsPersistenceMixin,
    QWidget,
):
    """Page for configuring application-level settings (including login)."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("SettingsInterface")

        self._worker: LoginWorker | None = None
        self._tag_dictionary_worker: TagDictionaryUpdateWorker | None = None
        self._loading_settings = False
        # The page is the heaviest widget tree in the app (about 65 MB) and the one
        # opened least, so it is built the first time it is shown, not at startup.
        self._built = False
        self._login_ui_state: tuple[bool, str] | None = None
        signal_bus.rules_changed.connect(self._reload_auto_enqueue_rules)

        # Startup auth: prefer cached token for faster boot; fallback to credential login.
        if download_manager.restore_cached_login():
            self._set_logged_in_ui(True, tr("✓ Signed in", "✓ 已登录", "✓ ログイン済み"))
            signal_bus.login_state_changed.emit(True)
        elif app_config.auth_enabled and app_config.username and app_config.password:
            self.ensure_built()  # the silent login reads the saved credentials from the form
            self._do_login(silent=True)

    def ensure_built(self):
        """Create the settings widgets (once)."""

        if self._built:
            return
        self._built = True
        self._build_ui()
        self._load_settings()
        if self._login_ui_state is not None:
            self._set_logged_in_ui(*self._login_ui_state)

    def showEvent(self, event):
        self.ensure_built()
        super().showEvent(event)

    def shutdown(self, *, timeout_ms: int = 30_000) -> bool:
        """Wait for an in-flight login request before destroying its QThread."""
        return stop_qthreads(
            [self._worker, self._tag_dictionary_worker],
            timeout_ms=timeout_ms,
        )

    # ── UI ────────────────────────────────────────────────────────────────────

    def show_shortcut_settings(self):
        """Jump to Settings → Keyboard Shortcuts (the F1 cheat sheet's button)."""

        self.ensure_built()
        self._settings_board.select_category("shortcuts")

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*PAGE_MARGINS)
        layout.setSpacing(16)

        layout.addWidget(TitleLabel(tr("Settings", "应用设置", "設定"), self))
        self._settings_board = SettingsSections(self)
        layout.addWidget(self._settings_board, 1)
        self._build_appearance_card()
        self._build_language_card()
        self._build_data_paths_card()
        self._build_account_card()
        self._build_quality_card()
        self._build_download_dir_card()
        self._build_behavior_card()
        self._build_concurrency_card()
        self._build_cover_performance_card()
        self._build_search_engine_card()
        self._build_search_limit_card()
        self._build_search_history_card()
        self._build_search_bridge_card()
        self._build_subscription_prompt_card()
        self._build_proxy_card()
        self._build_aria2_card()
        self._build_subscription_automation_card()
        self._build_download_policy_card()
        self._build_updates_card()

        # ── Window, request pacing and maintenance (self-applying) ───────────
        self._system_cards = SystemSettingsCards(self._settings_board, self)
        self._home_cards = HomeSettingsCards(self._settings_board, self)
        self._shortcut_cards = ShortcutSettingsCards(self._settings_board, self)

        # ── Save button ───────────────────────────────────────────────────────
        # Kept under the category list so it is reachable from every page.
        save_btn = PrimaryPushButton(tr("Save All Settings", "保存所有设置", "すべて保存"), self, FluentIcon.SAVE)
        save_btn.clicked.connect(self._save_settings)
        self._settings_board.nav_column.addWidget(save_btn)

    def refresh_theme_styles(self):
        # The navigation toggle stores an explicit mode; mirror it here.
        if self._built:
            self._sync_theme_combo()
