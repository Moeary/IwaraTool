"""Refresh sources and covers, with progress shown on the page."""

from __future__ import annotations

from PySide6.QtCore import Qt

from qfluentwidgets import InfoBarPosition, ProgressBar

from ...i18n import tr
from .components import SubscriptionRefreshWorker, SubscriptionThumbnailWorker
from .page_dependencies import InfoBar, download_manager, signal_bus


class SubscriptionRefreshingMixin:
    """Refresh sources and covers, with progress shown on the page."""

    def _refresh_current_source(self):
        source_id = self._current_source_id or self._selected_source_id()
        if not source_id:
            self._show_error(tr("Select a subscription source first", "请先选择一个订阅源", "購読元を選択してください"))
            return
        self._start_refresh(source_id, ignore_disabled=True)

    def _refresh_selected_sources(self, source_ids: list[int] | None = None):
        ids = list(source_ids or self._selected_source_ids_for_operation())
        if not ids:
            self._show_error(
                tr(
                    "Select one or more subscription sources first",
                    "请先选择一个或多个订阅源",
                    "先に1つ以上の購読元を選択してください",
                )
            )
            return
        self._start_refresh(ids, ignore_disabled=True)

    def _cache_current_source_covers(self):
        source_id = self._current_source_id or self._selected_source_id()
        if not source_id:
            self._show_error(tr("Select a subscription source first", "请先选择一个订阅源", "購読元を選択してください"))
            return
        if self._shutting_down or self._cover_cache_worker is not None:
            return
        # Snapshot the full source, independently of filters and later navigation.
        items = download_manager.get_subscription_items(source_id)
        requests = list({
            str(item["video_id"]): str(item.get("thumbnail_url") or "")
            for item in items if item.get("video_id")
        }.items())
        if not requests:
            self._show_error(tr("Refresh the source list first", "请先刷新订阅源的视频列表", "先に購読元の一覧を更新してください"))
            return
        self._cover_cache_worker = SubscriptionThumbnailWorker(
            requests, concurrency=self._cover_download_concurrency(),
        )
        self._cover_cache_worker.thumbnail_ready.connect(self._on_thumbnail_ready)
        self._cover_cache_worker.finished.connect(self._on_source_cover_cache_finished)
        self._cover_cache_worker.start()
        InfoBar.info(
            title=tr("Caching Covers", "正在缓存封面", "カバーをキャッシュ中"),
            content=tr(
                "Caching all known videos in this source; existing covers are reused.",
                "正在缓存此订阅源已收录的全部视频封面，已有缓存会直接复用；无需下载视频。",
                "この購読元の登録済み動画のカバーを保存します。既存のキャッシュは再利用します。",
            ),
            duration=4000, parent=self,
        )

    def _on_source_cover_cache_finished(self):
        worker = self._cover_cache_worker
        self._cover_cache_worker = None
        if worker is None:
            return
        succeeded, failed = worker.succeeded, worker.failed
        worker.deleteLater()
        if self._shutting_down:
            return
        InfoBar.info(
            title=tr("Cover Cache Complete", "封面缓存完成", "カバーのキャッシュ完了"),
            content=tr(
                f"Available: {succeeded}; failed: {failed}. Run again to retry missing covers.",
                f"本地可用 {succeeded} 张，失败 {failed} 张。可再次执行以重试缺失封面。",
                f"利用可能: {succeeded}、失敗: {failed}。再実行で未取得分を再試行できます。",
            ),
            duration=5000, parent=self,
        )

    def _refresh_current_covers(self):
        source_id = self._current_source_id or self._selected_source_id()
        if not source_id:
            self._show_error(tr("Select a subscription source first", "请先选择一个订阅源", "購読元を選択してください"))
            return
        self._thumbnail_force_refresh_ids.clear()
        started = self._start_thumbnail_worker_for_visible_items(force=True)
        if started:
            InfoBar.info(
                title=tr("Refreshing Covers", "正在刷新封面", "カバーを更新中"),
                content=tr(
                    "The current source covers are being refreshed.",
                    "正在刷新当前订阅源的视频封面。",
                    "現在の購読元のカバーを更新しています。",
                ),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=2500,
                parent=self,
            )

    def _refresh_all(self):
        hidden_feed = any(
            str(s.get("source_type", "") or "") == "feed" and int(s.get("enabled", 1) or 0)
            for s in download_manager.get_subscription_sources()
        )
        if not hidden_feed:
            self._start_refresh(None)
            return
        # A leftover account-feed source is not shown here; do not refresh it behind the user's back.
        ids = [int(s["id"]) for s in self._all_sources if int(s.get("enabled", 1) or 0)]
        if ids:
            self._start_refresh(ids)

    def _show_selected_source_all_items(self):
        source_id = self._selected_source_id() or self._current_source_id
        if not source_id:
            self._show_error(tr("Select a subscription source first", "请先选择一个订阅源", "購読元を選択してください"))
            return
        self._title_filter_mode_btn.setChecked(False)
        self._title_search_edit.clear()
        self._install_filter_combo.setCurrentIndex(0)
        self._new_filter_combo.setCurrentIndex(0)
        self._load_items(source_id)

    def _start_refresh(
        self,
        source_id: int | list[int] | None,
        *,
        ignore_disabled: bool = False,
    ):
        if self._worker and self._worker.isRunning():
            return
        self._set_refresh_actions_enabled(False)
        worker = SubscriptionRefreshWorker(source_id, ignore_disabled=ignore_disabled)
        worker.progress.connect(self._on_refresh_progress)
        worker.result_ready.connect(self._on_refresh_finished)
        worker.finished.connect(lambda worker=worker: self._release_worker("_worker", worker))
        self._worker = worker
        worker.start()

    def _set_refresh_actions_enabled(self, enabled: bool):
        for button_name in ("_refresh_current_btn", "_refresh_all_btn"):
            button = getattr(self, button_name, None)
            if button:
                button.setEnabled(enabled)

    def _on_refresh_progress(self, progress: dict):
        total = max(1, int(progress.get("total", 1) or 1))
        index = max(1, min(total, int(progress.get("index", 1) or 1)))
        started = str(progress.get("stage", "") or "") == "started"
        title = str(progress.get("title", "") or "")
        if self._refresh_info_bar is None:
            self._refresh_info_bar = InfoBar.info(
                title=tr("Refreshing Subscriptions", "正在刷新订阅", "購読を更新中"),
                content="",
                orient=Qt.Orientation.Horizontal,
                isClosable=False,
                duration=-1,
                position=InfoBarPosition.TOP,
                parent=self,
            )
            self._refresh_progress = ProgressBar(self._refresh_info_bar)
            self._refresh_progress.setFixedWidth(150)
            self._refresh_progress.setRange(0, total)
            self._refresh_info_bar.addWidget(self._refresh_progress)

        completed = index - 1 if started else index
        if self._refresh_progress:
            self._refresh_progress.setRange(0, total)
            self._refresh_progress.setValue(completed)
        content = tr(
            f"{index}/{total} · {'Refreshing' if started else 'Finished'} {title}",
            f"{index}/{total} · {'正在刷新' if started else '已完成'} {title}",
            f"{index}/{total} · {'更新中' if started else '完了'} {title}",
        )
        self._refresh_info_bar.content = content
        self._refresh_info_bar.contentLabel.setText(content)
        self._refresh_info_bar.adjustSize()

    def _clear_refresh_progress(self):
        info_bar = self._refresh_info_bar
        self._refresh_info_bar = None
        self._refresh_progress = None
        if info_bar:
            info_bar.close()

    def _on_refresh_finished(self, summary: dict):
        self._clear_refresh_progress()
        self._set_refresh_actions_enabled(True)
        self._refresh_sources_keep_current_items()
        errors = summary.get("errors") or []
        if errors:
            first = errors[0]
            InfoBar.warning(
                title=tr("Refresh Finished With Errors", "刷新完成但有错误", "一部更新失敗"),
                content=str(first.get("error", ""))[:180],
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=5000,
                parent=self,
            )
        else:
            InfoBar.success(
                title=tr("Refresh Finished", "刷新完成", "更新完了"),
                content=tr(
                    f"New {summary.get('new', 0)}, downloaded locally {summary.get('downloaded', 0)}, unavailable {summary.get('unavailable', 0)}, total {summary.get('total', 0)}",
                    f"新增 {summary.get('new', 0)}，本地已下载 {summary.get('downloaded', 0)}，不可下载 {summary.get('unavailable', 0)}，累计 {summary.get('total', 0)}",
                    f"新規 {summary.get('new', 0)}、保存済み {summary.get('downloaded', 0)}、保存不可 {summary.get('unavailable', 0)}、合計 {summary.get('total', 0)}",
                ),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=4000,
                parent=self,
            )
        signal_bus.log_message.emit(
            tr(
                f"[Subscriptions] refresh done: new={summary.get('new', 0)}, downloaded={summary.get('downloaded', 0)}, unavailable={summary.get('unavailable', 0)}, total={summary.get('total', 0)}",
                f"[订阅] 刷新完成: 新增={summary.get('new', 0)}, 已下载={summary.get('downloaded', 0)}, 不可下载={summary.get('unavailable', 0)}, 总计={summary.get('total', 0)}",
                f"[購読] 更新完了: 新規={summary.get('new', 0)}, 保存済み={summary.get('downloaded', 0)}, 保存不可={summary.get('unavailable', 0)}, 合計={summary.get('total', 0)}",
            )
        )
