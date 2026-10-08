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
    FluentIcon,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    PushButton,
    SpinBox,
    SubtitleLabel,
    SwitchButton,
)

from ..config import app_config
from ..core import autostart, backup
from ..i18n import tr
from ..logging_setup import get_logger, log_dir
from .ui_state import show_fluent_confirmation

logger = get_logger(__name__)


class SystemSettingsCards:
    """Builds the three cards into a ``SettingsSections`` board."""

    def __init__(self, board, owner: QWidget):
        self._owner = owner
        self._loading = True
        self._build_window_card(board)
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
            tr("Window & Startup", "窗口与启动", "ウィンドウと起動"),
        )
        self._tray_switch = SwitchButton(card)
        self._tray_switch.setEnabled(QSystemTrayIcon.isSystemTrayAvailable())
        self._tray_switch.checkedChanged.connect(self._on_tray_toggle)
        self._row(
            layout,
            tr(
                "Keep running in the system tray when the window is closed",
                "关闭窗口时最小化到系统托盘继续运行",
                "ウィンドウを閉じてもシステムトレイで動作を続ける",
            ),
            self._tray_switch,
            card,
        )
        self._autostart_switch = SwitchButton(card)
        self._autostart_switch.setEnabled(autostart.is_supported())
        self._autostart_switch.checkedChanged.connect(self._on_autostart_toggle)
        self._row(
            layout,
            tr("Launch at Windows login", "开机自动启动", "Windows ログイン時に起動"),
            self._autostart_switch,
            card,
        )
        board.add_card("window", card)

    def _build_request_card(self, board):
        card = board.create_card("request_policy")
        layout = self._card_layout(
            card,
            tr("Request Pacing", "请求节奏", "リクエスト間隔"),
            tr(
                "Spacing and retries for Iwara API requests. A larger interval lowers the risk of 429 blocks during big refreshes.",
                "Iwara API 请求的间隔与重试。批量刷新时调大间隔可降低被限流（429）的风险。",
                "Iwara API リクエストの間隔と再試行。大量更新時は間隔を広げると 429 を避けやすくなります。",
            ),
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
            tr("Minimum interval between requests", "请求最小间隔", "リクエスト最小間隔"),
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
            tr(
                "Retries on 429 / 5xx / network errors",
                "遇到 429 / 5xx / 网络错误时的重试次数",
                "429 / 5xx / ネットワークエラー時の再試行回数",
            ),
            self._retries_spin,
            card,
        )
        layout.addWidget(
            BodyLabel(
                tr(
                    "Extra X-Version salts (comma separated). Only needed if downloads start failing after a site change; they are tried before the built-in ones.",
                    "额外的 X-Version 盐值（逗号分隔）。仅在站点更换密钥导致下载失败时填写，会优先于内置值尝试。",
                    "追加の X-Version ソルト（カンマ区切り）。サイト側の変更でダウンロードが失敗する場合のみ設定します。組み込み値より先に試行されます。",
                ),
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
            tr("Backup & Logs", "备份与日志", "バックアップとログ"),
            tr(
                "Backups include settings, history, subscriptions, rules and the task queue. Account, login token and aria2 secret are never included; covers and caches are skipped.",
                "备份包含设置、历史、订阅、规则与任务队列，不含账号、登录 token 和 aria2 密钥，也不含封面与缓存。",
                "バックアップには設定・履歴・購読・ルール・タスクキューが含まれます。アカウント、ログイン token、aria2 の秘密情報とキャッシュは含まれません。",
            ),
        )
        row = QHBoxLayout()
        backup_btn = PushButton(tr("Back Up…", "备份…", "バックアップ…"), card, FluentIcon.SAVE)
        backup_btn.clicked.connect(self._create_backup)
        restore_btn = PushButton(tr("Restore…", "恢复…", "復元…"), card, FluentIcon.HISTORY)
        restore_btn.clicked.connect(self._restore_backup)
        logs_btn = PushButton(tr("Open Log Folder", "打开日志目录", "ログフォルダーを開く"), card, FluentIcon.FOLDER)
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
        self._interval_spin.setValue(max(0, app_config.request_min_interval_ms))
        self._retries_spin.setValue(max(0, app_config.request_max_retries))
        self._salts_edit.setText(app_config.x_version_salts)
        self._loading = False

    def _set(self, key: str, value):
        if not self._loading:
            setattr(app_config, key, value)

    def _on_tray_toggle(self, checked: bool):
        self._set("minimize_to_tray", bool(checked))

    def _on_autostart_toggle(self, checked: bool):
        if self._loading:
            return
        try:
            autostart.set_enabled(bool(checked))
        except OSError as exc:
            logger.warning("Could not change launch-at-login", exc_info=True)
            self._loading = True
            self._autostart_switch.setChecked(autostart.is_enabled())
            self._loading = False
            self._notify(False, str(exc))

    # ── Maintenance actions ──────────────────────────────────────────────────

    def _notify(self, ok: bool, message: str):
        (InfoBar.success if ok else InfoBar.error)(
            title=tr("Done", "完成", "完了") if ok else tr("Failed", "失败", "失敗"),
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
            tr("Back up IwaraTool data", "备份 IwaraTool 数据", "IwaraTool のデータをバックアップ"),
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
        self._notify(True, tr(f"Saved {len(stored)} files to {path}", f"已保存 {len(stored)} 个文件到 {path}", f"{len(stored)} 件を {path} に保存しました"))

    def _restore_backup(self):
        path, _ = QFileDialog.getOpenFileName(
            self._owner,
            tr("Restore IwaraTool data", "恢复 IwaraTool 数据", "IwaraTool のデータを復元"),
            "",
            "ZIP (*.zip)",
        )
        if not path:
            return
        if not show_fluent_confirmation(
            self._owner,
            tr("Restore backup", "恢复备份", "バックアップの復元"),
            tr(
                "Settings, history, subscriptions and rules will be replaced after the next start.",
                "设置、历史、订阅和规则将在下次启动时被替换。",
                "設定・履歴・購読・ルールは次回起動時に置き換えられます。",
            ),
            informative=tr(
                "Your current files are kept under data/backup_before_restore, and your login stays as it is. Restart the app to apply.",
                "当前文件会保留在 data/backup_before_restore，登录状态不受影响。请重启程序以应用。",
                "現在のファイルは data/backup_before_restore に保存され、ログイン状態は維持されます。適用するには再起動してください。",
            ),
            yes_text=tr("Stage restore", "准备恢复", "復元を準備"),
            no_text=tr("Cancel", "取消", "キャンセル"),
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
        self._notify(
            True,
            tr(
                f"{len(staged)} files staged. Restart IwaraTool to finish restoring.",
                f"已暂存 {len(staged)} 个文件，重启 IwaraTool 后完成恢复。",
                f"{len(staged)} 件を準備しました。IwaraTool を再起動すると復元が完了します。",
            ),
        )

    def _open_logs(self):
        directory = log_dir()
        os.makedirs(directory, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(directory))
