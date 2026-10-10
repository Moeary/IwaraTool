"""Add selected search results to the download queue."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt

from qfluentwidgets import InfoBar, InfoBarPosition

from ...core.search.models import IWARA_IMAGE_SOURCE_KIND, SearchPlaylist, SearchVideo
from ...i18n import tr
from ..worker_lifecycle import on_finished
from .page_dependencies import download_manager
from .oreno_workers import SearchQueueResolveWorker


class SearchQueueingMixin:
    """Add selected search results to the download queue."""

    @staticmethod
    def _is_queueable(value: dict[str, Any]) -> bool:
        """Videos and whole playlists can be queued; image posts cannot."""

        data = value.get("data")
        if isinstance(data, SearchPlaylist):
            return True
        return isinstance(data, SearchVideo) and (
            data.source_kind in {"iwara", "oreno3d"} or bool(data.download_video_id)
        )

    def _queue_selected(self):
        videos: list[SearchVideo] = []
        playlists: list[SearchPlaylist] = []
        for value in self._selected_data():
            data = value.get("data")
            if not self._is_queueable(value):
                continue
            if isinstance(data, SearchPlaylist):
                playlists.append(data)
            elif value.get("kind") == "video" and isinstance(data, SearchVideo):
                videos.append(data)
        if not videos and not playlists:
            if any(
                isinstance(value.get("data"), SearchVideo)
                and value["data"].source_kind == IWARA_IMAGE_SOURCE_KIND
                for value in self._selected_data()
            ):
                self._show_warning(tr(
                    "Image posts cannot be downloaded; open them in the browser instead.",
                    "图片作品暂不支持下载，请在浏览器中打开查看。",
                    "画像投稿はダウンロードできません。ブラウザーで開いてください。",
                ))
                return
            self._show_warning(tr("Select at least one video", "请至少选择一个视频", "動画を1件以上選択してください"))
            return

        rule_id = self._rule_picker.selected_rule_id()
        self._rule_picker.apply_selected(show_notice=False)
        if playlists:
            # The manager expands each playlist URL into its videos, exactly
            # as when the URL is pasted on the download page.
            for playlist in playlists:
                download_manager.add_url(playlist.source_url, rule_id=rule_id)
            InfoBar.success(
                title=tr("Added to queue", "已加入队列", "キューに追加"),
                content=tr(
                    f"Fetching videos from {len(playlists)} playlist(s)",
                    f"正在获取 {len(playlists)} 个播放列表中的视频",
                    f"{len(playlists)} 件のプレイリストから動画を取得中",
                ),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )
            if not videos:
                return

        if any(video.source_kind == "oreno3d" for video in videos):
            if self._queue_resolve_worker is not None and self._queue_resolve_worker.isRunning():
                return
            worker = SearchQueueResolveWorker(
                videos,
                concurrency=self._search_resolution_concurrency(),
            )
            self._queue_resolve_worker = worker
            self._queue_resolve_rule_id = rule_id
            self._queue_selected_btn.setEnabled(False)
            self._status_label.setText(
                tr(
                    "Resolving selected Oreno3D links…",
                    "正在解析选中的 Oreno3D 下载链接…",
                    "選択したOreno3Dリンクを解決中…",
                )
            )
            worker.result_ready.connect(self._on_queue_resolved)
            on_finished(worker, self, self._cleanup_queue_resolve_worker)
            worker.start()
            return

        self._enqueue_video_ids(
            [video.download_video_id or video.video_id for video in videos],
            rule_id=rule_id,
        )

    def _enqueue_video_ids(
        self,
        ids: list[str],
        *,
        skipped: int = 0,
        errors: list[str] | None = None,
        rule_id: str = "",
    ):
        ids = [str(video_id or "").strip() for video_id in ids if str(video_id or "").strip()]
        errors = errors or []
        if not ids:
            message = tr(
                "No selected item has an Iwara download link.",
                "选中的项目没有可用的 Iwara 下载链接。",
                "選択した項目にIwaraダウンロードリンクがありません。",
            )
            if errors:
                message += f" {errors[0]}"
            self._show_warning(message)
            self._sync_selection_buttons()
            return
        selected_rule_id = str(rule_id or self._rule_picker.selected_rule_id())
        accepted = download_manager.enqueue_video_ids(
            ids,
            source_label=tr("Search", "搜索", "検索"),
            rule_id=selected_rule_id,
        )
        suffix = tr(
            f"; skipped {skipped} item(s)" if skipped else "",
            f"；已跳过 {skipped} 个无下载链接的项目" if skipped else "",
            f"；{skipped}件をスキップ" if skipped else "",
        )
        InfoBar.success(
            title=tr("Added to queue", "已加入队列", "キューに追加"),
            content=tr(
                f"Accepted {accepted} video(s){suffix}",
                f"已接受 {accepted} 个视频{suffix}",
                f"{accepted} 件を追加しました{suffix}",
            ),
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=3000,
            parent=self,
        )
        if errors:
            self._show_warning(errors[0])

    def _on_queue_resolved(self, result: object):
        if not isinstance(result, dict):
            self._show_warning(tr("Could not resolve selected links", "无法解析选中的链接", "選択したリンクを解決できません"))
            return
        self._enqueue_video_ids(
            list(result.get("ids") or []),
            skipped=int(result.get("skipped") or 0),
            errors=[str(error) for error in result.get("errors") or []],
            rule_id=self._queue_resolve_rule_id,
        )
        self._sync_selection_buttons()

    def _cleanup_queue_resolve_worker(self, worker: SearchQueueResolveWorker):
        if self._queue_resolve_worker is worker:
            self._queue_resolve_worker = None
            self._queue_resolve_rule_id = ""
        worker.deleteLater()
        self._sync_selection_buttons()
