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
from ...i18n import tr
from ...signal_bus import signal_bus
from ..ui_state import show_fluent_confirmation
from .page_dependencies import download_manager


class SettingsDownloadCardsMixin:
    """Download directory, behavior, concurrency and runtime policy cards."""

    def _build_download_dir_card(self):
        dir_card = self._settings_board.create_card("download_dir")
        dir_layout = QVBoxLayout(dir_card)
        dir_layout.setContentsMargins(20, 16, 20, 16)
        dir_layout.setSpacing(10)

        dir_layout.addWidget(SubtitleLabel(tr("Download Directory", "下载目录", "ダウンロード先"), dir_card))
        dir_layout.addWidget(
            BodyLabel(
                tr(
                    "Video files are saved here and grouped by author",
                    "视频文件保存位置，按作者名建立子文件夹",
                    "動画保存先。作者名ごとにサブフォルダーを作成します",
                ),
                dir_card,
            )
        )

        dir_row = QHBoxLayout()
        self._dir_edit = LineEdit(dir_card)
        self._dir_edit.setPlaceholderText(tr("Choose download directory…", "选择下载目录…", "保存先を選択…"))
        self._dir_edit.setReadOnly(True)

        browse_btn = ToolButton(FluentIcon.EDIT, dir_card)
        open_dir_btn = ToolButton(FluentIcon.FOLDER, dir_card)
        open_dir_btn.setToolTip(tr("Open download folder", "打开下载目录", "保存先を開く"))
        open_dir_btn.clicked.connect(lambda: self._open_local_folder(self._dir_edit.text().strip()))
        browse_btn.setToolTip(tr("Browse…", "浏览…", "参照…"))
        browse_btn.clicked.connect(self._browse_dir)

        dir_row.addWidget(self._dir_edit, stretch=1)
        dir_row.addWidget(browse_btn)
        dir_row.addWidget(open_dir_btn)
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
        cleanup_btn = PushButton(tr("Clean *_temp", "清理 _temp", "_temp を削除"), dir_card, FluentIcon.DELETE)
        cleanup_btn.clicked.connect(self._confirm_clear_temp_files)
        cleanup_row.addWidget(cleanup_btn)
        dir_layout.addLayout(cleanup_row)
        self._settings_board.add_card("download_dir", dir_card)

    def _build_behavior_card(self):
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

    def _build_concurrency_card(self):
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

    def _build_download_policy_card(self):
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
