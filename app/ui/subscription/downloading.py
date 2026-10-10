"""Queue subscription items for download and mark them seen / downloaded / moved."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog

from qfluentwidgets import InfoBarPosition, PrimaryPushButton

from ...config import app_config
from ...i18n import tr
from ..download_page import FilterDialog, option_button_style
from .components import SubscriptionEnqueueWorker, _CONTROL_HEIGHT
from .page_dependencies import InfoBar, download_manager, signal_bus


class SubscriptionDownloadingMixin:
    """Queue subscription items for download and mark them seen / downloaded / moved."""

    def _mark_selected_seen(self):
        ids = self._operation_video_ids()
        if not ids:
            ids = [str(item.get("video_id", "") or "") for item in self._visible_items if item.get("is_new")]
        download_manager.mark_subscription_items_seen(ids)
        self._refresh_sources_keep_current_items()

    def _mark_selected_downloaded_moved(self, video_ids: list[str] | None = None):
        ids = (
            list(video_ids)
            if isinstance(video_ids, (list, tuple, set))
            else self._operation_video_ids()
        )
        if not ids:
            self._show_error(tr("Select one or more videos first", "请先选中右侧列表里的一个或多个视频", "先に右側リストで動画を選択してください"))
            return
        marked = download_manager.mark_subscription_items_downloaded(ids)
        self._refresh_sources_keep_current_items()
        InfoBar.success(
            title=tr("Marked", "已标记", "マーク完了"),
            content=tr(
                f"Marked {marked} videos as downloaded/moved",
                f"已将 {marked} 个视频标记为已下载（移走）",
                f"{marked} 件を保存済み（移動済み）にしました",
            ),
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=3000,
            parent=self,
        )

    def _restore_selected_downloaded_moved(self, video_ids: list[str] | None = None):
        ids = (
            list(video_ids)
            if isinstance(video_ids, (list, tuple, set))
            else self._operation_video_ids()
        )
        if not ids:
            self._show_error(tr("Select one or more moved videos first", "请先选中一个或多个已移走的视频", "先に移動済み動画を選択してください"))
            return
        restored = download_manager.restore_subscription_items_downloaded(ids)
        self._refresh_sources_keep_current_items()
        InfoBar.success(
            title=tr("Restored", "已还原", "解除完了"),
            content=tr(
                f"Restored {restored} moved records",
                f"已还原 {restored} 个已移走记录",
                f"移動済み記録を {restored} 件解除しました",
            ),
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=3000,
            parent=self,
        )

    def _download_selected(self, video_ids: list[str] | None = None):
        ids = (
            list(video_ids)
            if isinstance(video_ids, (list, tuple, set))
            else self._operation_video_ids()
        )
        if not ids:
            self._show_error(tr("Select one or more videos first", "请先选中右侧列表里的一个或多个视频", "先に右側リストで動画を選択してください"))
            return
        self._enqueue_ids(ids)

    def _open_filter_dialog(self):
        dlg = FilterDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            signal_bus.download_options_changed.emit()
            InfoBar.success(
                title=tr("Filter rules saved", "筛选条件已保存", "フィルター条件を保存しました"),
                content=tr(
                    "Subscription downloads will use the same filter rules as the download workbench.",
                    "订阅下载会使用和下载工作台相同的筛选规则。",
                    "購読保存にもダウンロード画面と同じフィルター条件を使います。",
                ),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=2500,
                parent=self,
            )

    def _make_download_option_button(self, text: str, tooltip: str) -> PrimaryPushButton:
        button = PrimaryPushButton(text, self)
        button._base_text = text
        button.setCheckable(True)
        button.setMinimumWidth(126)
        button.setFixedHeight(_CONTROL_HEIGHT)
        button.setToolTip(tooltip)
        return button

    def _sync_download_option_buttons(self):
        if app_config.mark_submitted_as_downloaded and app_config.download_video_file:
            app_config.download_video_file = False
        if not app_config.mark_submitted_as_downloaded and not app_config.download_video_file:
            app_config.download_video_file = True
        if not hasattr(self, "_option_download_video_btn"):
            return
        self._syncing_download_options = True
        try:
            self._set_download_option_button_state(self._option_download_video_btn, app_config.download_video_file)
            self._set_download_option_button_state(self._option_mark_downloaded_btn, app_config.mark_submitted_as_downloaded)
            self._set_download_option_button_state(self._option_download_thumb_btn, app_config.download_thumbnail)
            self._set_download_option_button_state(self._option_collect_nfo_btn, app_config.collect_nfo_info)
        finally:
            self._syncing_download_options = False

    def _set_download_option_button_state(self, button: PrimaryPushButton, checked: bool):
        button.setChecked(checked)
        base_text = str(getattr(button, "_base_text", button.text()) or "")
        state = tr("On", "开", "オン") if checked else tr("Off", "关", "オフ")
        button.setText(f"{base_text}  {state}")
        button.setStyleSheet(option_button_style(checked))

    def _on_download_video_option_clicked(self, checked: bool):
        if self._syncing_download_options:
            return
        app_config.download_video_file = bool(checked)
        app_config.mark_submitted_as_downloaded = not bool(checked)
        signal_bus.download_options_changed.emit()

    def _on_mark_downloaded_option_clicked(self, checked: bool):
        if self._syncing_download_options:
            return
        app_config.mark_submitted_as_downloaded = bool(checked)
        app_config.download_video_file = not bool(checked)
        signal_bus.download_options_changed.emit()

    def _on_download_thumb_option_clicked(self, checked: bool):
        if self._syncing_download_options:
            return
        app_config.download_thumbnail = bool(checked)
        signal_bus.download_options_changed.emit()

    def _on_collect_nfo_option_clicked(self, checked: bool):
        if self._syncing_download_options:
            return
        app_config.collect_nfo_info = bool(checked)
        signal_bus.download_options_changed.emit()

    def _apply_selected_rule_for_download(self):
        # Rules are independent from the list's browse filter. Reapplying here
        # keeps keyboard/automation calls consistent without rerendering rows.
        if hasattr(self, "_rule_picker"):
            self._rule_picker.apply_selected(show_notice=False)

    def _download_selected_with_rule(self, video_ids: list[str] | None = None):
        ids = (
            list(video_ids)
            if isinstance(video_ids, (list, tuple, set))
            else self._operation_video_ids()
        )
        self._apply_selected_rule_for_download()
        self._enqueue_ids(ids)

    def _download_new_with_rule(self):
        ids = [
            str(item.get("video_id", "") or "")
            for item in self._visible_items
            if item.get("is_new") and not item.get("downloaded")
        ]
        self._apply_selected_rule_for_download()
        self._enqueue_ids(ids)

    def _download_visible_with_rule(self):
        ids = [
            str(item.get("video_id", "") or "")
            for item in self._visible_items
            if not item.get("downloaded")
        ]
        self._apply_selected_rule_for_download()
        self._enqueue_ids(ids)

    def _download_new(self):
        ids = [
            str(item.get("video_id", "") or "")
            for item in self._visible_items
            if item.get("is_new") and not item.get("downloaded")
        ]
        self._enqueue_ids(ids)

    def _download_visible(self):
        ids = [
            str(item.get("video_id", "") or "")
            for item in self._visible_items
            if not item.get("downloaded")
        ]
        self._enqueue_ids(ids)

    def _enqueue_ids(self, ids: list[str], *, rule_id: str = ""):
        ids = [video_id for video_id in ids if video_id]
        if not ids:
            self._show_error(tr("No downloadable videos in current selection", "当前选择没有可下载视频", "保存可能な動画がありません"))
            return
        if self._enqueue_worker and self._enqueue_worker.isRunning():
            self._show_error(tr("Queue operation is still running", "队列操作仍在进行中", "キュー操作が実行中です"))
            return
        selected_rule_id = str(
            rule_id
            or (self._rule_picker.selected_rule_id() if hasattr(self, "_rule_picker") else "")
        )
        worker = SubscriptionEnqueueWorker(ids, rule_id=selected_rule_id)
        worker.result_ready.connect(self._on_enqueue_finished)
        worker.finished.connect(lambda worker=worker: self._release_worker("_enqueue_worker", worker))
        self._enqueue_worker = worker
        worker.start()

    def _on_enqueue_finished(self, result: dict):
        queued = int(result.get("queued", 0) or 0)
        marked = int(result.get("marked", 0) or 0)
        thumbnail = int(result.get("thumbnail", 0) or 0)
        nfo = int(result.get("nfo", 0) or 0)
        failed = int(result.get("failed", 0) or 0)
        skipped_unavailable = int(result.get("skipped_unavailable", 0) or 0)
        mode = str(result.get("mode", "") or "")
        self._refresh_sources_keep_current_items()
        if mode == "metadata":
            title = tr("Processed", "已处理", "処理完了")
            content = tr(
                f"Marked {marked}, thumbnails {thumbnail}, NFO {nfo}, failed {failed}, skipped unavailable {skipped_unavailable}",
                f"已标记 {marked}，封面 {thumbnail}，NFO {nfo}，失败 {failed}，跳过不可下载 {skipped_unavailable}",
                f"記録 {marked}、サムネイル {thumbnail}、NFO {nfo}、失敗 {failed}、保存不可スキップ {skipped_unavailable}",
            )
        elif mode == "empty" and skipped_unavailable:
            title = tr("Skipped", "已跳过", "スキップ")
            content = tr(
                f"Skipped {skipped_unavailable} unavailable videos",
                f"已跳过 {skipped_unavailable} 个不可下载视频",
                f"保存不可の動画を {skipped_unavailable} 件スキップしました",
            )
        else:
            title = tr("Added To Queue", "已加入队列", "キューに追加")
            content = tr(
                f"Queued {queued} videos, skipped unavailable {skipped_unavailable}",
                f"已加入 {queued} 个视频，跳过不可下载 {skipped_unavailable}",
                f"{queued} 件を追加、保存不可スキップ {skipped_unavailable}",
            )
        bar = InfoBar.warning if (mode == "empty" and skipped_unavailable) else InfoBar.success
        bar(
            title=title,
            content=content,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=3000,
            parent=self,
        )
