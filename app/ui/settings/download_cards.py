"""Download directory, behavior, concurrency and runtime policy cards."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import (
    BodyLabel,
    ComboBox,
    FluentIcon,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    PushButton,
    Slider,
    SpinBox,
    SubtitleLabel,
    SwitchButton,
    ToolButton,
)

from ...config import app_config
from ...presentation.settings import values as setting_values
from ...presentation.settings.texts import stext
from ...signal_bus import signal_bus
from ..common.fluent_dialogs import show_fluent_confirmation
from .page_dependencies import download_manager


class SettingsDownloadCardsMixin:
    """Download directory, behavior, concurrency and runtime policy cards."""

    def _build_download_dir_card(self):
        dir_card = self._settings_board.create_card("download_dir")
        dir_layout = QVBoxLayout(dir_card)
        dir_layout.setContentsMargins(20, 16, 20, 16)
        dir_layout.setSpacing(10)

        dir_layout.addWidget(SubtitleLabel(stext("download_directory"), dir_card))
        dir_layout.addWidget(
            BodyLabel(
                stext("video_files_are_saved_here_and"),
                dir_card,
            )
        )

        dir_row = QHBoxLayout()
        self._dir_edit = LineEdit(dir_card)
        self._dir_edit.setPlaceholderText(stext("choose_download_directory"))
        self._dir_edit.setReadOnly(True)

        browse_btn = ToolButton(FluentIcon.EDIT, dir_card)
        open_dir_btn = ToolButton(FluentIcon.FOLDER, dir_card)
        open_dir_btn.setToolTip(stext("open_download_folder"))
        open_dir_btn.clicked.connect(lambda: self._open_local_folder(self._dir_edit.text().strip()))
        browse_btn.setToolTip(stext("browse"))
        browse_btn.clicked.connect(self._browse_dir)

        dir_row.addWidget(self._dir_edit, stretch=1)
        dir_row.addWidget(browse_btn)
        dir_row.addWidget(open_dir_btn)
        dir_layout.addLayout(dir_row)

        cleanup_row = QHBoxLayout()
        cleanup_row.addWidget(
            BodyLabel(
                stext("clean_stale_temp_files_in_download"),
                dir_card,
            )
        )
        cleanup_row.addStretch()
        cleanup_btn = PushButton(stext("clean_temp"), dir_card, FluentIcon.DELETE)
        cleanup_btn.clicked.connect(self._confirm_clear_temp_files)
        cleanup_row.addWidget(cleanup_btn)
        dir_layout.addLayout(cleanup_row)
        self._settings_board.add_card("download_dir", dir_card)

    def _build_behavior_card(self):
        name_card = self._settings_board.create_card("behavior")
        name_layout = QVBoxLayout(name_card)
        name_layout.setContentsMargins(20, 16, 20, 16)
        name_layout.setSpacing(10)
        name_layout.addWidget(SubtitleLabel(stext("download_behavior"), name_card))

        skip_row = QHBoxLayout()
        skip_row.addWidget(
            BodyLabel(
                stext("skip_existing_completed_files_in_batch"),
                name_card,
            )
        )
        skip_row.addStretch()
        self._skip_existing_switch = SwitchButton(name_card)
        skip_row.addWidget(self._skip_existing_switch)
        name_layout.addLayout(skip_row)

        direct_row = QHBoxLayout()
        direct_label = BodyLabel(
            stext("download_a_single_video_at_once"),
            name_card,
        )
        direct_label.setWordWrap(True)
        direct_row.addWidget(direct_label, 1)
        self._direct_single_switch = SwitchButton(name_card)
        direct_row.addWidget(self._direct_single_switch)
        name_layout.addLayout(direct_row)

        click_row = QHBoxLayout()
        click_row.addWidget(BodyLabel(stext("completed_card_click_action"), name_card))
        click_row.addStretch()
        self._completed_click_combo = ComboBox(name_card)
        self._completed_click_combo.addItems(
            [
                stext("open_folder"),
                stext("open_player"),
            ]
        )
        self._completed_click_combo.setFixedWidth(180)
        click_row.addWidget(self._completed_click_combo)
        name_layout.addLayout(click_row)

        self._settings_board.add_card("behavior", name_card)

    def _build_concurrency_card(self):
        conc_card = self._settings_board.create_card("concurrency")
        conc_layout = QVBoxLayout(conc_card)
        conc_layout.setContentsMargins(20, 16, 20, 16)
        conc_layout.setSpacing(10)

        conc_layout.addWidget(SubtitleLabel(stext("concurrent_downloads"), conc_card))
        conc_layout.addWidget(
            BodyLabel(
                stext("max_tasks_in_resolving_queued_downloading"),
                conc_card,
            )
        )

        conc_row = QHBoxLayout()
        self._conc_slider = Slider(Qt.Orientation.Horizontal, conc_card)
        self._conc_slider.setRange(1, 10)
        self._conc_slider.valueChanged.connect(self._on_concurrency_changed)
        self._conc_slider.setMinimumWidth(160)
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
        self._build_stall_rows(conc_card, conc_layout)
        self._settings_board.add_card("concurrency", conc_card)

    def _build_stall_rows(self, conc_card: QWidget, conc_layout: QVBoxLayout):
        """Idle-task watchdog: timeout and automatic restore."""

        stall_row = QHBoxLayout()
        stall_row.addWidget(
            BodyLabel(
                stext("auto_cancel_idle_task_after_seconds"),
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
                stext("auto_restore_idle_cancelled_tasks_when"),
                conc_card,
            )
        )
        auto_restore_row.addStretch()
        self._auto_restore_stalled_switch = SwitchButton(conc_card)
        self._auto_restore_stalled_switch.setToolTip(
            stext("only_restores_tasks_cancelled_by_the")
        )
        self._auto_restore_stalled_switch.checkedChanged.connect(self._on_auto_restore_stalled_toggle)
        auto_restore_row.addWidget(self._auto_restore_stalled_switch)
        conc_layout.addLayout(auto_restore_row)
        self._build_auto_retry_rows(conc_card, conc_layout)

    def _build_auto_retry_rows(self, conc_card: QWidget, conc_layout: QVBoxLayout):
        """Automatic retry of failed tasks, decided by the error message."""

        retry_row = QHBoxLayout()
        retry_row.addWidget(
            BodyLabel(
                stext("auto_retry_failed_tasks_with_network"),
                conc_card,
            )
        )
        retry_row.addStretch()
        self._auto_retry_failed_switch = SwitchButton(conc_card)
        self._auto_retry_failed_switch.setToolTip(
            stext("broken_connections_incompleteread_timeouts_and_5")
        )
        self._auto_retry_failed_switch.checkedChanged.connect(self._on_auto_retry_failed_toggle)
        retry_row.addWidget(self._auto_retry_failed_switch)
        conc_layout.addLayout(retry_row)

        self._auto_retry_widget = QWidget(conc_card)
        detail_layout = QVBoxLayout(self._auto_retry_widget)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(8)

        attempts_row = QHBoxLayout()
        attempts_row.addWidget(
            BodyLabel(
                stext("max_automatic_retries_per_task"),
                self._auto_retry_widget,
            )
        )
        attempts_row.addStretch()
        self._auto_retry_attempts_spin = SpinBox(self._auto_retry_widget)
        self._auto_retry_attempts_spin.setRange(1, 20)
        self._auto_retry_attempts_spin.setFixedWidth(130)
        self._auto_retry_attempts_spin.valueChanged.connect(self._on_auto_retry_attempts_changed)
        attempts_row.addWidget(self._auto_retry_attempts_spin)
        detail_layout.addLayout(attempts_row)

        self._auto_retry_keywords_edit = self._auto_retry_keyword_row(
            detail_layout,
            stext("always_retry_errors_containing"),
            stext("e_g_http_403_ssl"),
        )
        self._auto_skip_keywords_edit = self._auto_retry_keyword_row(
            detail_layout,
            stext("never_retry_errors_containing"),
            stext("e_g_404"),
        )
        detail_layout.addWidget(
            BodyLabel(
                stext("separate_keywords_with_commas_never_wins"),
                self._auto_retry_widget,
            )
        )
        conc_layout.addWidget(self._auto_retry_widget)

    def _auto_retry_keyword_row(self, layout: QVBoxLayout, label: str, placeholder: str) -> LineEdit:
        row = QHBoxLayout()
        row.addWidget(BodyLabel(label, self._auto_retry_widget))
        row.addStretch()
        edit = LineEdit(self._auto_retry_widget)
        edit.setMinimumWidth(220)
        edit.setClearButtonEnabled(True)
        edit.setPlaceholderText(placeholder)
        edit.editingFinished.connect(self._on_auto_retry_keywords_finished)
        row.addWidget(edit)
        layout.addLayout(row)
        return edit

    def _build_download_policy_card(self):
        policy_card = self._settings_board.create_card("download_policy")
        policy_layout = QVBoxLayout(policy_card)
        policy_layout.setContentsMargins(20, 16, 20, 16)
        policy_layout.setSpacing(10)
        policy_layout.addWidget(
            SubtitleLabel(stext("download_policy"), policy_card)
        )

        speed_row = QHBoxLayout()
        speed_row.addWidget(BodyLabel(stext("global_speed_limit"), policy_card))
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
        schedule_row.addWidget(BodyLabel(stext("scheduled_starts"), policy_card))
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
                stext("only_new_tasks_start_inside_the"),
                policy_card,
            )
        )
        self._settings_board.add_card("download_policy", policy_card)

    def _browse_dir(self):
        current = self._dir_edit.text() or os.path.expanduser("~")
        chosen = QFileDialog.getExistingDirectory(
            self,
            stext("choose_download_directory_2"),
            current,
        )
        if chosen:
            self._dir_edit.setText(chosen)
            app_config.download_dir = chosen

    def _on_concurrency_changed(self, value: int):
        self._conc_input.setText(str(value))
        setting_values.set_max_concurrent(value)

    def _on_concurrency_input_finished(self):
        value = setting_values.parse_int(
            self._conc_input.text(), current=self._conc_slider.value(), low=1, high=10
        )
        self._conc_slider.setValue(value)
        self._conc_input.setText(str(value))

    def _on_stall_timeout_input_finished(self):
        value = setting_values.set_stall_timeout(self._stall_timeout_edit.text())
        self._stall_timeout_edit.setText(str(value))

    def _on_auto_restore_stalled_toggle(self, checked: bool):
        if self._loading_settings:
            return
        app_config.auto_restore_stalled_cancelled = bool(checked)

    def _on_auto_retry_failed_toggle(self, checked: bool):
        self._auto_retry_widget.setVisible(bool(checked))
        if self._loading_settings:
            return
        app_config.auto_retry_failed_enabled = bool(checked)

    def _on_auto_retry_attempts_changed(self, value: int):
        if self._loading_settings:
            return
        app_config.auto_retry_failed_max_attempts = int(value)

    def _on_auto_retry_keywords_finished(self):
        if self._loading_settings:
            return
        setting_values.set_retry_keywords(
            self._auto_retry_keywords_edit.text(), self._auto_skip_keywords_edit.text()
        )

    def _confirm_clear_temp_files(self):
        if not show_fluent_confirmation(
            self,
            stext("confirm_cleanup"),
            stext("all_temp_files_in_download_directory"),
            informative=stext("this_action_cannot_be_undone_continue"),
            yes_text=stext("delete_temp_files"),
            no_text=stext("cancel"),
        ):
            return

        removed, failed = download_manager.clear_temp_files()
        ok, title, content, log = setting_values.temp_cleanup_report(removed, failed)
        signal_bus.log_message.emit(log)
        (InfoBar.success if ok else InfoBar.warning)(
            title=title,
            content=content,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=3000 if ok else 4000,
            parent=self,
        )
