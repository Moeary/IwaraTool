"""Subscription prompt, background automation, cover performance and update cards."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout

from qfluentwidgets import (
    BodyLabel,
    ComboBox,
    FluentIcon,
    InfoBar,
    InfoBarPosition,
    PushButton,
    SpinBox,
    SubtitleLabel,
    SwitchButton,
)

from ...config import app_config
from ...core.download.policy import normalize_hhmm
from ...core.rules import BUILTIN_DEFAULT_RULE_ID, rule_store
from ...i18n import tr
from ...signal_bus import signal_bus
from .page_dependencies import download_manager


class SettingsSubscriptionCardsMixin:
    """Subscription prompt, background automation, cover performance and update cards."""

    def _build_cover_performance_card(self):
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

    def _build_subscription_prompt_card(self):
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

    def _build_subscription_automation_card(self):
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

        confirm_row = QHBoxLayout()
        confirm_row.addWidget(
            BodyLabel(
                tr("Auto queued videos also wait for confirmation", "自动入队也需在下载工作台确认", "自動追加も確認待ちにする"),
                automation_card,
            )
        )
        confirm_row.addStretch()
        self._auto_enqueue_confirm_switch = SwitchButton(automation_card)
        confirm_row.addWidget(self._auto_enqueue_confirm_switch)
        automation_layout.addLayout(confirm_row)

        rule_row = QHBoxLayout()
        rule_row.addWidget(BodyLabel(tr("Matching rule", "匹配规则", "照合ルール"), automation_card))
        self._auto_enqueue_rule_combo = ComboBox(automation_card)
        self._auto_enqueue_rule_combo.setFixedWidth(220)
        rule_row.addWidget(self._auto_enqueue_rule_combo)
        rule_row.addStretch()
        automation_layout.addLayout(rule_row)

        refresh_now_btn = PushButton(
            tr("Refresh Now", "立即刷新", "今すぐ更新"),
            automation_card,
            FluentIcon.SYNC,
        )
        refresh_now_btn.clicked.connect(self._refresh_subscriptions_now)
        automation_layout.addWidget(refresh_now_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        self._settings_board.add_card("subscription_automation", automation_card)

    def _build_updates_card(self):
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
        check_update_btn = PushButton(
            tr("Check Now", "立即检查", "今すぐ確認"),
            update_card,
            FluentIcon.UPDATE,
        )
        check_update_btn.clicked.connect(self._check_updates_now)
        update_layout.addWidget(check_update_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        self._settings_board.add_card("updates", update_card)

    def _on_subscription_refresh_workers_changed(self, value: int):
        if self._loading_settings:
            return
        value = max(1, min(8, int(value)))
        app_config.set_ui_value("subscription_refresh_workers_v1", value)

    def _on_subscription_incremental_toggle(self, checked: bool):
        if self._loading_settings:
            return
        app_config.set_ui_value("subscription_incremental_refresh_v1", bool(checked))

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
        from ...core.background_services import background_service

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
        from ...core.background_services import background_service

        background_service.check_updates_now()

    def _save_background_settings(self):
        app_config.subscription_auto_refresh_enabled = self._auto_refresh_switch.isChecked()
        app_config.subscription_refresh_interval_minutes = self._auto_refresh_interval_spin.value()
        app_config.desktop_notifications_enabled = self._desktop_notification_switch.isChecked()
        app_config.subscription_auto_enqueue_enabled = self._auto_enqueue_switch.isChecked()
        app_config.subscription_auto_enqueue_confirm = self._auto_enqueue_confirm_switch.isChecked()
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
