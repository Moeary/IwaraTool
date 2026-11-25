"""Settings cards for window behaviour, request pacing and data maintenance.

Every control applies as soon as it changes, so these cards need no part in
the page-wide "Save All Settings" flow.
"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QVBoxLayout, QWidget, QSystemTrayIcon

from qfluentwidgets import (
    BodyLabel,
    ComboBox,
    FluentIcon,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    PushButton,
    SpinBox,
    SubtitleLabel,
    SwitchButton,
)

from ...config import app_config
from ...core import autostart, backup
from ...core.playback import video_player
from ...presentation.settings import values as setting_values
from ...presentation.settings.texts import stext
from ...logging_setup import get_logger, log_dir
from ..common.click_dispatch import ACTIONS as CLICK_ACTIONS, action_label
from ..common.fluent_dialogs import show_fluent_confirmation

logger = get_logger(__name__)


class SystemSettingsCards:
    """Builds the three cards into a ``SettingsSections`` board."""

    def __init__(self, board, owner: QWidget):
        self._owner = owner
        self._loading = True
        self._build_window_card(board)
        self._build_clicks_card(board)
        self._build_playback_card(board)
        self._build_request_card(board)
        self._build_maintenance_card(board)
        self.load()

    # ── Layout helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _card_layout(card, title: str, description: str = "") -> QVBoxLayout:
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)
        layout.addWidget(SubtitleLabel(title, card))
        if description:
            layout.addWidget(BodyLabel(description, card))
        return layout

    @staticmethod
    def _row(layout: QVBoxLayout, label: str, control: QWidget, parent: QWidget):
        row = QHBoxLayout()
        row.addWidget(BodyLabel(label, parent))
        row.addStretch()
        row.addWidget(control)
        layout.addLayout(row)

    # ── Cards ────────────────────────────────────────────────────────────────

    def _build_window_card(self, board):
        card = board.create_card("window")
        layout = self._card_layout(
            card,
            stext("window_startup"),
        )
        self._tray_switch = SwitchButton(card)
        self._tray_switch.setEnabled(QSystemTrayIcon.isSystemTrayAvailable())
        self._tray_switch.checkedChanged.connect(self._on_tray_toggle)
        self._row(
            layout,
            stext("keep_running_in_the_system_tray"),
            self._tray_switch,
            card,
        )
        self._autostart_switch = SwitchButton(card)
        self._autostart_switch.setEnabled(autostart.is_supported())
        self._autostart_switch.checkedChanged.connect(self._on_autostart_toggle)
        self._row(
            layout,
            stext("launch_at_windows_login"),
            self._autostart_switch,
            card,
        )
        self._startup_combo = ComboBox(card)
        for key, label in self._startup_pages():
            self._startup_combo.addItem(label)
            self._startup_combo.setItemData(self._startup_combo.count() - 1, key)
        self._startup_combo.setFixedWidth(200)
        self._startup_combo.currentIndexChanged.connect(self._on_startup_page_changed)
        self._row(
            layout,
            stext("page_shown_at_launch"),
            self._startup_combo,
            card,
        )
        board.add_card("window", card)

    @staticmethod
    def _startup_pages() -> list[tuple[str, str]]:
        return [
            ("home", stext("home")),
            ("subscriptions", stext("subscriptions")),
            ("search", stext("search")),
            ("download", stext("download_hub")),
            ("repair", stext("repair")),
            ("history", stext("history")),
            ("rules", stext("rules")),
            ("tools", stext("tools")),
            ("settings", stext("settings")),
        ]

    _PLAYER_MODES = (video_player.MODE_SYSTEM, video_player.MODE_BUILTIN, video_player.MODE_CUSTOM)
    _PREVIEW_QUALITIES = ("Source", "540", "360")

    def _build_clicks_card(self, board):
        card = board.create_card("card_clicks")
        layout = self._card_layout(
            card,
            stext("clicking_results"),
            stext("for_the_search_page_subscriptions_and"),
        )
        self._click_combo = ComboBox(card)
        self._double_click_combo = ComboBox(card)
        for combo in (self._click_combo, self._double_click_combo):
            for action in CLICK_ACTIONS:
                combo.addItem(action_label(action), userData=action)
            combo.setFixedWidth(220)
        self._click_combo.currentIndexChanged.connect(
            lambda index: self._set("media_click_action", self._click_combo.itemData(index))
        )
        self._double_click_combo.currentIndexChanged.connect(
            lambda index: self._set("media_double_click_action", self._double_click_combo.itemData(index))
        )
        self._row(layout, stext("click"), self._click_combo, card)
        self._row(layout, stext("double_click"), self._double_click_combo, card)
        board.add_card("card_clicks", card)

    def _build_playback_card(self, board):
        card = board.create_card("playback")
        layout = self._card_layout(
            card,
            stext("video_playback"),
            stext("when_a_video_is_played_in"),
        )
        self._player_combo = ComboBox(card)
        self._player_combo.addItems(
            [
                stext("system_default_player"),
                stext("built_in_player"),
                stext("custom_command"),
            ]
        )
        self._player_combo.currentIndexChanged.connect(self._on_player_mode_changed)
        self._row(layout, stext("player_for_downloaded_videos"), self._player_combo, card)

        self._player_command_widget = QWidget(card)
        command_row = QHBoxLayout(self._player_command_widget)
        command_row.setContentsMargins(0, 0, 0, 0)
        self._player_command_edit = LineEdit(self._player_command_widget)
        self._player_command_edit.setPlaceholderText(
            r'"C:\Program Files\mpv\mpv.exe" --fs "{file}"'
        )
        self._player_command_edit.editingFinished.connect(
            lambda: self._set("preview_player_command", self._player_command_edit.text().strip())
        )
        browse_btn = PushButton(stext("browse"), self._player_command_widget, FluentIcon.FOLDER)
        browse_btn.clicked.connect(self._browse_player)
        command_row.addWidget(self._player_command_edit, 1)
        command_row.addWidget(browse_btn)
        layout.addWidget(self._player_command_widget)
        layout.addWidget(
            BodyLabel(
                stext("file_is_replaced_by_the_video"),
                card,
            )
        )

        self._quality_combo = ComboBox(card)
        self._quality_combo.addItems(
            [
                stext("source_largest"),
                "540p",
                "360p",
            ]
        )
        self._quality_combo.currentIndexChanged.connect(
            lambda index: self._set("preview_quality", self._PREVIEW_QUALITIES[max(0, index)])
        )
        self._row(layout, stext("streaming_quality"), self._quality_combo, card)
        board.add_card("playback", card)

    def _on_player_mode_changed(self, index: int):
        mode = self._PLAYER_MODES[max(0, min(index, len(self._PLAYER_MODES) - 1))]
        self._set("preview_player_mode", mode)
        self._player_command_widget.setVisible(mode == video_player.MODE_CUSTOM)

    def _browse_player(self):
        path, _ = QFileDialog.getOpenFileName(
            self._owner, stext("choose_a_player"), "", ""
        )
        if path:
            quoted = f'"{path}" "{{file}}"' if " " in path else f'{path} "{{file}}"'
            self._player_command_edit.setText(quoted)
            self._set("preview_player_command", quoted)

    def _build_request_card(self, board):
        card = board.create_card("request_policy")
        layout = self._card_layout(
            card,
            stext("request_pacing"),
            stext("spacing_and_retries_for_iwara_api"),
        )
        self._interval_spin = SpinBox(card)
        self._interval_spin.setRange(0, 5000)
        self._interval_spin.setSingleStep(50)
        self._interval_spin.setSuffix(" ms")
        self._interval_spin.valueChanged.connect(
            lambda value: self._set("request_min_interval_ms", int(value))
        )
        self._row(
            layout,
            stext("minimum_interval_between_requests"),
            self._interval_spin,
            card,
        )
        self._retries_spin = SpinBox(card)
        self._retries_spin.setRange(0, 5)
        self._retries_spin.valueChanged.connect(
            lambda value: self._set("request_max_retries", int(value))
        )
        self._row(
            layout,
            stext("retries_on_429_5xx_network_errors"),
            self._retries_spin,
            card,
        )
        layout.addWidget(
            BodyLabel(
                stext("extra_x_version_salts_comma_separated"),
                card,
            )
        )
        self._salts_edit = LineEdit(card)
        self._salts_edit.editingFinished.connect(
            lambda: self._set("x_version_salts", self._salts_edit.text().strip())
        )
        layout.addWidget(self._salts_edit)
        board.add_card("request_policy", card)

    def _build_maintenance_card(self, board):
        card = board.create_card("maintenance")
        layout = self._card_layout(
            card,
            stext("backup_logs"),
            stext("backups_include_settings_history_subscriptions_r"),
        )
        row = QHBoxLayout()
        backup_btn = PushButton(stext("back_up"), card, FluentIcon.SAVE)
        backup_btn.clicked.connect(self._create_backup)
        restore_btn = PushButton(stext("restore"), card, FluentIcon.HISTORY)
        restore_btn.clicked.connect(self._restore_backup)
        logs_btn = PushButton(stext("open_log_folder"), card, FluentIcon.FOLDER)
        logs_btn.clicked.connect(self._open_logs)
        for button in (backup_btn, restore_btn, logs_btn):
            row.addWidget(button)
        row.addStretch()
        layout.addLayout(row)
        board.add_card("maintenance", card)

    # ── State ────────────────────────────────────────────────────────────────

    def load(self):
        self._loading = True
        self._tray_switch.setChecked(app_config.minimize_to_tray)
        self._autostart_switch.setChecked(autostart.is_enabled())
        startup = str(app_config.startup_page or "home")
        self._startup_combo.setCurrentIndex(max(0, self._startup_combo.findData(startup)))
        self._click_combo.setCurrentIndex(max(0, self._click_combo.findData(app_config.media_click_action)))
        self._double_click_combo.setCurrentIndex(
            max(0, self._double_click_combo.findData(app_config.media_double_click_action))
        )
        mode = video_player.player_mode()
        self._player_combo.setCurrentIndex(self._PLAYER_MODES.index(mode))
        self._player_command_edit.setText(app_config.preview_player_command)
        self._player_command_widget.setVisible(mode == video_player.MODE_CUSTOM)
        quality = app_config.preview_quality
        self._quality_combo.setCurrentIndex(
            self._PREVIEW_QUALITIES.index(quality) if quality in self._PREVIEW_QUALITIES else 1
        )
        self._interval_spin.setValue(max(0, app_config.request_min_interval_ms))
        self._retries_spin.setValue(max(0, app_config.request_max_retries))
        self._salts_edit.setText(app_config.x_version_salts)
        self._loading = False

    def _set(self, key: str, value):
        if not self._loading:
            setattr(app_config, key, value)

    def _on_tray_toggle(self, checked: bool):
        self._set("minimize_to_tray", bool(checked))

    def _on_startup_page_changed(self, index: int):
        self._set("startup_page", str(self._startup_combo.itemData(index) or "home"))

    def _on_autostart_toggle(self, checked: bool):
        if self._loading:
            return
        error = setting_values.set_autostart(checked)
        if error:
            logger.warning("Could not change launch-at-login: %s", error)
            self._loading = True
            self._autostart_switch.setChecked(autostart.is_enabled())
            self._loading = False
            self._notify(False, error)

    # ── Maintenance actions ──────────────────────────────────────────────────

    def _notify(self, ok: bool, message: str):
        (InfoBar.success if ok else InfoBar.error)(
            title=stext("done") if ok else stext("failed"),
            content=message,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=6000,
            parent=self._owner.window(),
        )

    def _create_backup(self):
        default = os.path.join(app_config.app_data_dir, backup.default_backup_name())
        path, _ = QFileDialog.getSaveFileName(
            self._owner,
            stext("back_up_iwaratool_data"),
            default,
            "ZIP (*.zip)",
        )
        if not path:
            return
        try:
            stored = backup.create_backup(app_config.app_data_dir, path)
        except Exception as exc:
            logger.exception("Backup failed")
            self._notify(False, str(exc))
            return
        self._notify(True, setting_values.backup_saved_text(len(stored), path))

    def _restore_backup(self):
        path, _ = QFileDialog.getOpenFileName(
            self._owner,
            stext("restore_iwaratool_data"),
            "",
            "ZIP (*.zip)",
        )
        if not path:
            return
        if not show_fluent_confirmation(
            self._owner,
            stext("restore_backup"),
            stext("settings_history_subscriptions_and_rules_will"),
            informative=stext("your_current_files_are_kept_under"),
            yes_text=stext("stage_restore"),
            no_text=stext("cancel"),
        ):
            return
        try:
            staged = backup.stage_restore(path, app_config.app_data_dir)
        except backup.BackupError as exc:
            self._notify(False, str(exc))
            return
        except Exception as exc:
            logger.exception("Restore staging failed")
            self._notify(False, str(exc))
            return
        self._notify(True, setting_values.restore_staged_text(len(staged)))

    def _open_logs(self):
        directory = log_dir()
        os.makedirs(directory, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(directory))
