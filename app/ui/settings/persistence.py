"""Fill the settings widgets from the config and save them back."""

from __future__ import annotations

from PySide6.QtCore import Qt

from qfluentwidgets import InfoBar, InfoBarPosition

from ...config import app_config
from ...core.download.policy import normalize_hhmm
from ...i18n import tr
from .page_dependencies import download_manager


class SettingsPersistenceMixin:
    """Fill the settings widgets from the config and save them back."""

    def _load_settings(self):
        self._loading_settings = True
        self._load_download_settings()
        self._load_network_settings()
        self._load_background_settings()
        self._load_display_settings()
        self._load_search_settings()
        self._load_worker_settings()
        self._load_account_settings()
        self._loading_settings = False

    def _load_download_settings(self):
        self._dir_edit.setText(app_config.download_dir)
        self._conc_slider.setValue(app_config.max_concurrent)
        self._conc_input.setText(str(app_config.max_concurrent))
        self._stall_timeout_edit.setText(str(app_config.task_stall_timeout_seconds))
        self._auto_restore_stalled_switch.setChecked(app_config.auto_restore_stalled_cancelled)

    def _load_network_settings(self):
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

    def _load_background_settings(self):
        self._auto_refresh_switch.setChecked(app_config.subscription_auto_refresh_enabled)
        self._auto_refresh_interval_spin.setValue(app_config.subscription_refresh_interval_minutes)
        self._desktop_notification_switch.setChecked(app_config.desktop_notifications_enabled)
        self._auto_enqueue_switch.setChecked(app_config.subscription_auto_enqueue_enabled)
        self._auto_enqueue_confirm_switch.setChecked(bool(app_config.subscription_auto_enqueue_confirm))
        self._reload_auto_enqueue_rules(app_config.subscription_auto_enqueue_rule_id)
        self._speed_limit_switch.setChecked(app_config.global_speed_limit_enabled)
        self._speed_limit_spin.setValue(max(1, app_config.global_speed_limit_kib or 1024))
        self._schedule_switch.setChecked(app_config.download_schedule_enabled)
        self._schedule_start_edit.setText(normalize_hhmm(app_config.download_schedule_start))
        self._schedule_end_edit.setText(normalize_hhmm(app_config.download_schedule_end))
        self._update_check_switch.setChecked(app_config.update_check_enabled)
        self._skip_existing_switch.setChecked(app_config.skip_existing_files)
        self._direct_single_switch.setChecked(bool(app_config.direct_download_single))

    def _load_display_settings(self):
        self._sync_theme_combo()
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

    def _load_search_settings(self):
        self._search_limit_switch.setChecked(app_config.search_limit_enabled)
        self._search_limit_edit.setText(str(max(1, app_config.search_limit_count)))
        self._search_limit_edit.setEnabled(app_config.search_limit_enabled)
        self._search_history_limit_spin.setValue(app_config.search_history_limit)
        self._search_auto_search_switch.setChecked(app_config.search_auto_search_enabled)
        self._search_source_combo.blockSignals(True)
        self._search_source_combo.setCurrentIndex(
            1 if app_config.search_default_source == "oreno3d" else 0
        )
        self._search_source_combo.blockSignals(False)
        self._search_chinese_oreno_switch.setChecked(bool(app_config.search_chinese_via_oreno3d))
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

    def _load_worker_settings(self):
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

    def _load_account_settings(self):
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

    def _save_settings(self):
        if not self._built:
            return  # nothing has been shown, so nothing can have been edited
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
        app_config.direct_download_single = self._direct_single_switch.isChecked()
        app_config.completed_task_click_action = (
            "player" if self._completed_click_combo.currentIndex() == 1 else "folder"
        )
        app_config.subscription_prompt_mode = ["ask", "always", "never"][self._subscription_prompt_combo.currentIndex()]
        self._on_search_limit_input_finished()
        app_config.search_limit_enabled = self._search_limit_switch.isChecked()
        app_config.search_history_limit = self._search_history_limit_spin.value()
        app_config.search_auto_search_enabled = self._search_auto_search_switch.isChecked()
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
