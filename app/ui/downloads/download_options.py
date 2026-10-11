"""Video / cover / NFO / mark-downloaded toggles and the filter dialog button."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QWidget

from qfluentwidgets import FluentIcon, InfoBar, InfoBarPosition, PrimaryPushButton

from ...config import app_config
from ...i18n import tr
from ...signal_bus import signal_bus
from .filter_dialog import FilterDialog, option_button_style


class DownloadOptionsMixin:
    """Video / cover / NFO / mark-downloaded toggles and the filter dialog button."""

    def _make_option_button(self, text: str, tooltip: str, parent: QWidget, _icon: FluentIcon) -> PrimaryPushButton:
        button = PrimaryPushButton(text, parent)
        button._base_text = text
        button.setCheckable(True)
        button.setMinimumHeight(48)
        button.setToolTip(tooltip)
        return button

    def _emit_options_changed(self):
        signal_bus.download_options_changed.emit()

    def _on_filter_state_changed(self):
        state = tr("enabled", "启用", "有効") if app_config.filter_enabled else tr("disabled", "关闭", "無効")
        signal_bus.log_message.emit(
            tr(
                f"[Filter] {state}",
                f"[筛选] 已{state}",
                f"[フィルター] {state}",
            )
        )

    def _on_download_video_clicked(self, checked: bool):
        if self._syncing_options:
            return
        app_config.download_video_file = bool(checked)
        app_config.mark_submitted_as_downloaded = not bool(checked)
        self._emit_options_changed()

    def _on_download_thumb_clicked(self, checked: bool):
        if self._syncing_options:
            return
        app_config.download_thumbnail = bool(checked)
        self._emit_options_changed()

    def _on_collect_nfo_clicked(self, checked: bool):
        if self._syncing_options:
            return
        app_config.collect_nfo_info = bool(checked)
        self._emit_options_changed()

    def _on_mark_downloaded_clicked(self, checked: bool):
        if self._syncing_options:
            return
        app_config.mark_submitted_as_downloaded = bool(checked)
        app_config.download_video_file = not bool(checked)
        self._emit_options_changed()

    def _sync_option_controls(self):
        if app_config.mark_submitted_as_downloaded and app_config.download_video_file:
            app_config.download_video_file = False
        if not app_config.mark_submitted_as_downloaded and not app_config.download_video_file:
            app_config.download_video_file = True

        self._syncing_options = True
        try:
            self._set_option_button_state(self._download_video_btn, app_config.download_video_file)
            self._set_option_button_state(self._mark_downloaded_btn, app_config.mark_submitted_as_downloaded)
            self._set_option_button_state(self._download_thumb_btn, app_config.download_thumbnail)
            self._set_option_button_state(self._collect_nfo_btn, app_config.collect_nfo_info)
        finally:
            self._syncing_options = False

    def _set_option_button_state(self, button: PrimaryPushButton, checked: bool):
        button.setChecked(checked)
        state = tr("On", "开", "オン") if checked else tr("Off", "关", "オフ")
        base_text = str(getattr(button, "_base_text", button.text()) or "")
        button.setText(f"{base_text}  {state}")
        button.setStyleSheet(option_button_style(checked))

    def _open_filter_dialog(self):
        dlg = FilterDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._on_filter_state_changed()
            self._emit_options_changed()
            InfoBar.success(
                title=tr("Filter rules saved", "筛选条件已保存", "フィルター条件を保存しました"),
                content=tr(
                    "New tasks will be filtered during resolving",
                    "新提交任务将按当前筛选规则解析后决定是否下载",
                    "新規タスクは解析時に現在の条件で判定されます",
                ),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=2500,
                parent=self,
            )
