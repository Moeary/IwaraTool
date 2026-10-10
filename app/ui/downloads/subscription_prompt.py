"""Offer to subscribe to the author or playlist of a submitted download."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog

from qfluentwidgets import BodyLabel, CheckBox, MessageBoxBase, PushButton, SubtitleLabel

from ...config import app_config
from ...core.manager import download_manager
from ...i18n import tr
from ...signal_bus import signal_bus


class _SubscriptionPromptDialog(MessageBoxBase):
    """Fluent three-choice prompt used after downloading an author or playlist."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.choice = "once"
        self.title_label = SubtitleLabel(
            tr("Add Subscription", "加入订阅列表", "購読に追加"), self
        )
        self.content_label = BodyLabel(
            tr(
                "Add this author/playlist to the local subscription list?",
                "是否把这个作者/播放列表加入本地订阅列表？",
                "この作者/プレイリストをローカル購読に追加しますか？",
            ),
            self,
        )
        self.content_label.setWordWrap(True)
        self.no_remind = CheckBox(
            tr("Do not ask again", "下次不再提醒", "次回から確認しない"), self
        )

        self.viewLayout.addWidget(self.title_label)
        self.viewLayout.addWidget(self.content_label)
        self.viewLayout.addWidget(self.no_remind)
        self.yesButton.setText(tr("Add This Time", "本次加入", "今回追加"))
        self.cancelButton.setText(tr("No", "不加入", "追加しない"))
        self.always_button = PushButton(
            tr("Always Add", "以后都自动加入", "常に追加"), self.buttonGroup
        )
        self.buttonLayout.insertWidget(
            1, self.always_button, 1, Qt.AlignmentFlag.AlignVCenter
        )
        self.always_button.clicked.connect(self._accept_always)
        self.widget.setMinimumWidth(620)

    def _accept_always(self):
        self.choice = "always"
        self.accept()


class DownloadSubscriptionPromptMixin:
    """Offer to subscribe to the author or playlist of a submitted download."""

    def _maybe_add_download_source_to_subscription(self, url: str):
        candidate = download_manager.detect_subscription_source(url)
        if not candidate:
            return
        kind, key = candidate
        mode = app_config.subscription_prompt_mode
        if mode == "never":
            return
        if mode == "always":
            self._add_subscription_source(kind, key)
            return

        box = _SubscriptionPromptDialog(self)
        accepted = box.exec() == QDialog.DialogCode.Accepted
        if accepted and box.choice == "always":
            app_config.subscription_prompt_mode = "always"
            self._add_subscription_source(kind, key)
        elif accepted:
            if box.no_remind.isChecked():
                app_config.subscription_prompt_mode = "never"
            self._add_subscription_source(kind, key)
        elif box.no_remind.isChecked():
            app_config.subscription_prompt_mode = "never"

    def _add_subscription_source(self, kind: str, key: str):
        source_id = download_manager.add_detected_subscription_source(kind, key)
        if source_id:
            signal_bus.log_message.emit(
                tr(
                    f"[Subscriptions] added local source: {kind}:{key}",
                    f"[订阅] 已加入本地订阅源：{kind}:{key}",
                    f"[購読] ローカル購読元を追加: {kind}:{key}",
                )
            )
            signal_bus.subscription_source_added.emit(source_id)
