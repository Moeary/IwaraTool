"""Resolve a queued task: fetch video info, pick the source and decide to skip or download."""

from __future__ import annotations

import json
import os
from typing import Any

from ...config import app_config
from ...i18n import tr
from ...logging_setup import get_logger
from ...signal_bus import signal_bus
from ..models import DownloadTask, TaskStatus
from ..task_metadata import (
    _author_fields_from_user,
    _compact_video_raw_json,
    _dict_or_empty,
    _subscription_download_block_from_error,
    _subscription_download_block_from_video_info,
)


logger = get_logger(__name__)


class ResolvingMixin:
    """Resolve a queued task: fetch video info, pick the source and decide to skip or download."""

    def _resolve_task(self, task_id: str):
        try:
            self._resolve_task_impl(task_id)
        except Exception as exc:
            logger.exception("Resolve failed for task %s", task_id)
            signal_bus.log_message.emit(
                tr(
                    f"[Resolve error] {task_id} -> {exc}",
                    f"[解析异常] {task_id} → {exc}",
                    f"[解析エラー] {task_id} -> {exc}",
                )
            )
            self._fail_task(
                task_id,
                tr(
                    f"Unexpected resolve error: {exc}",
                    f"解析阶段异常: {exc}",
                    f"解析中に予期しないエラー: {exc}",
                ),
            )

    def _resolve_task_impl(self, task_id: str):
        task = self._tasks.get(task_id)
        if not task:
            return
        rule_payload = self._task_rule_payload(task)
        if self._cancel_if_requested(task_id):
            return
        video_info = self._fetch_task_video_info(task, task_id)
        if video_info is None:
            return
        meta = self._store_task_metadata(task, task_id, video_info)
        if self._cancel_if_requested(task_id):
            return
        if not self._task_passes_filters(task_id, meta, rule_payload):
            return
        resolved = self._resolve_download_url(task, task_id, video_info, meta)
        if resolved is None:
            return
        dl_url, quality = resolved
        self._queue_resolved_download(task, task_id, meta, rule_payload, dl_url, quality)

    def _cancel_if_requested(self, task_id: str) -> bool:
        """End the task as cancelled when the user asked for it; True if so."""

        if not self._is_cancel_requested(task_id):
            return False
        self._cancel_task_terminal(
            task_id,
            tr("Cancelled by user", "用户已中断", "ユーザーが中断しました"),
        )
        return True

    def _fetch_task_video_info(self, task, task_id: str) -> dict[str, Any] | None:
        """The video's API info, or None once the task has been failed / cancelled."""

        signal_bus.log_message.emit(
            tr(
                f"[Resolve] Fetching video info: {task.video_id}",
                f"[解析] 开始获取视频信息: {task.video_id}",
                f"[解析] 動画情報を取得中: {task.video_id}",
            )
        )
        self._touch_task_activity(task_id)

        video_info, err = self._api_call("get_video_info", task.video_id)
        self._touch_task_activity(task_id)
        if self._cancel_if_requested(task_id):
            return None
        if not video_info:
            download_state, download_reason = _subscription_download_block_from_error(err)
            if download_state:
                self.subscriptions.update_item_download_state(task.video_id, download_state, download_reason or err)
            self._fail_task(
                task_id,
                tr(
                    f"Failed to fetch video info: {err}",
                    f"获取视频信息失败: {err}",
                    f"動画情報の取得に失敗: {err}",
                ),
            )
            signal_bus.log_message.emit(
                tr(
                    f"[Failed] {task.video_id} -> {err}",
                    f"[失败] {task.video_id} → {err}",
                    f"[失敗] {task.video_id} -> {err}",
                )
            )
            return None

        unavailable_reason = self._video_unavailable_reason(video_info)
        if unavailable_reason:
            download_state, download_reason = _subscription_download_block_from_video_info(video_info)
            if download_state:
                self.subscriptions.update_item_download_state(task.video_id, download_state, download_reason or unavailable_reason)
            self._fail_task(task_id, unavailable_reason)
            signal_bus.log_message.emit(
                tr(
                    f"[Unavailable] {task.video_id} -> {unavailable_reason}",
                    f"[不可用] {task.video_id} → {unavailable_reason}",
                    f"[利用不可] {task.video_id} -> {unavailable_reason}",
                )
            )
            return None
        return video_info

    def _store_task_metadata(self, task, task_id: str, video_info: dict[str, Any]) -> dict[str, Any]:
        """Copy the video's metadata onto the task; returns the fields later steps need."""

        user_info = _dict_or_empty(video_info.get("user"))
        file_info = _dict_or_empty(video_info.get("file"))
        title: str = video_info.get("title", task.video_id) or task.video_id
        author, username = _author_fields_from_user(user_info)
        meta = {
            "title": title,
            "author": author,
            "username": username,
            "published_at": str(video_info.get("createdAt", "") or ""),
            "likes": int(video_info.get("numLikes", 0) or 0),
            "views": int(video_info.get("numViews", 0) or 0),
            "slug": str(video_info.get("slug", "") or ""),
            "rating": str(video_info.get("rating", "") or ""),
            "duration": int(file_info.get("duration", 0) or 0),
            "comments": int(video_info.get("numComments", 0) or 0),
            "raw_tags": video_info.get("tags", []),
        }
        with self._lock:
            self._apply_task_metadata(
                task,
                title=title,
                author=author,
                username=username,
                published_at=meta["published_at"],
                likes=meta["likes"],
                views=meta["views"],
                slug=meta["slug"],
                rating=meta["rating"],
                duration=meta["duration"],
                comments=meta["comments"],
                tags_json=json.dumps(meta["raw_tags"], ensure_ascii=False),
                raw_json=_compact_video_raw_json(video_info),
                file_url=str(video_info.get("fileUrl", "") or ""),
                file_id=str(file_info.get("id", "") or ""),
                thumbnail_index=int(video_info.get("thumbnail", 0) or 0),
            )
            self._touch_task_activity_locked(task_id)
        return meta

    def _task_passes_filters(self, task_id: str, meta: dict[str, Any], rule_payload: dict[str, Any]) -> bool:
        raw_tags = meta["raw_tags"]
        passed_filter, filter_reason = self._passes_filters(
            title=meta["title"],
            likes=meta["likes"],
            views=meta["views"],
            published_at=meta["published_at"],
            tags=raw_tags if isinstance(raw_tags, list) else [],
            payload=rule_payload,
        )
        if not passed_filter:
            self._skip_task(
                task_id,
                tr(
                    f"Filtered out: {filter_reason}",
                    f"筛选不通过: {filter_reason}",
                    f"フィルター不一致: {filter_reason}",
                ),
            )
        return passed_filter

    def _resolve_download_url(self, task, task_id: str, video_info: dict[str, Any], meta: dict[str, Any]):
        """``(url, quality)`` for the preferred quality, or None once the task has ended."""

        title, author = meta["title"], meta["author"]
        signal_bus.log_message.emit(
            tr(
                f"[Resolve] \"{title}\" by {author}",
                f"[解析] 《{title}》 by {author}",
                f"[解析] 「{title}」 by {author}",
            )
        )

        # Pass quality preference and a logging callback
        pref_quality = app_config.preferred_quality
        signal_bus.log_message.emit(
            tr(
                f"[Resolve] Preferred quality: {pref_quality}, fetching source list...",
                f"[解析] 首选画质: {pref_quality}，开始获取文件列表…",
                f"[解析] 優先画質: {pref_quality}、ソース一覧を取得中...",
            )
        )

        def _log(msg: str):
            self._touch_task_activity(task_id)
            signal_bus.log_message.emit(msg)

        if self._cancel_if_requested(task_id):
            return None

        dl_url, quality, err2 = self._api_call(
            "get_download_info",
            video_info,
            preferred_quality=pref_quality,
            log_cb=_log,
        )
        self._touch_task_activity(task_id)
        if self._cancel_if_requested(task_id):
            return None
        if not dl_url:
            self._fail_task(
                task_id,
                tr(
                    f"Failed to resolve download URL: {err2}",
                    f"解析下载链接失败: {err2}",
                    f"ダウンロードURLの解決に失敗: {err2}",
                ),
            )
            signal_bus.log_message.emit(
                tr(
                    f"[Failed] \"{title}\" resolve failed -> {err2}",
                    f"[失败] 《{title}》 解析失败 → {err2}",
                    f"[失敗] 「{title}」解析失敗 -> {err2}",
                )
            )
            return None

        self.subscriptions.update_item_download_state(task.video_id, "", "")

        signal_bus.log_message.emit(
            tr(
                f"[Resolved] \"{title}\" quality={quality}",
                f"[解析完成] 《{title}》 画质={quality}",
                f"[解析完了] 「{title}」画質={quality}",
            )
        )
        return dl_url, quality

    def _queue_resolved_download(
        self, task, task_id: str, meta: dict[str, Any], rule_payload: dict[str, Any], dl_url: str, quality,
    ):
        """Name the output file, then skip an existing one or start downloading."""

        title = meta["title"]
        output_rel_path = self._build_output_relative_path(
            title=title,
            video_id=task.video_id,
            author=meta["author"],
            username=meta["username"],
            published_at=meta["published_at"],
            quality=quality or "",
            likes=meta["likes"],
            views=meta["views"],
            comments=meta["comments"],
            duration=meta["duration"],
            slug=meta["slug"],
            rating=meta["rating"],
            filename_template=rule_payload["filename_template"],
        )
        file_path = os.path.join(app_config.download_dir, output_rel_path)
        filename = os.path.basename(file_path)

        if (
            app_config.skip_existing_files
            and os.path.exists(file_path)
            and os.path.getsize(file_path) > 0
        ):
            with self._lock:
                task.download_url = dl_url
                task.quality = quality or ""
                task.filename = filename
                task.file_path = file_path
            signal_bus.log_message.emit(
                tr(
                    f"[Skipped] \"{title}\" already exists locally, marked completed",
                    f"[跳过] 《{title}》 本地已存在，标记完成",
                    f"[スキップ] 「{title}」はローカルに存在するため完了扱い",
                )
            )
            self._complete_task(task_id)
            return

        with self._lock:
            task.download_url = dl_url
            task.quality = quality or ""
            task.filename = filename
            task.file_path = file_path
            if task.cancel_requested:
                task.status = TaskStatus.CANCELLING
            else:
                task.status = TaskStatus.QUEUED_DOWNLOAD
                self._touch_task_activity_locked(task_id)

        if self._cancel_if_requested(task_id):
            return

        signal_bus.task_status_changed.emit(task_id, TaskStatus.QUEUED_DOWNLOAD.value)
        self._schedule_task_persist()
        # Immediately transition to download — slot is already counted
        self._start_downloading(task_id)

    def _video_unavailable_reason(self, video_info: dict[str, Any]) -> str:
        file_url = str(video_info.get("fileUrl", "") or "")
        file_info = video_info.get("file")
        message = str(video_info.get("message", "") or "")
        embed = str(video_info.get("embedUrl", "") or "")
        private = bool(video_info.get("private"))
        status = str(video_info.get("status", "") or "")
        unlisted = bool(video_info.get("unlisted"))

        if file_url:
            return ""
        if "youtube" in embed or "youtu.be" in embed:
            return tr(
                f"This video is an external embed and cannot be downloaded: {embed}",
                f"该作品是外部嵌入视频，无法直接下载：{embed}",
                f"この動画は外部埋め込みのため直接保存できません: {embed}",
            )
        if message == "errors.privateVideo" or private:
            if not self._current_token():
                return tr(
                    "Private video. Please login and try again.",
                    "私有作品，请先登录后重试。",
                    "非公開動画です。ログインして再試行してください。",
                )
            return tr(
                "Private video. The current logged-in account has no permission to download it.",
                "私有作品，当前登录账号没有权限下载。",
                "非公開動画です。現在のログインアカウントには保存権限がありません。",
            )
        if not isinstance(file_info, dict):
            detail = ", ".join(
                part
                for part in [
                    f"status={status}" if status else "",
                    "unlisted=true" if unlisted else "",
                ]
                if part
            )
            suffix = f" ({detail})" if detail else ""
            return tr(
                f"No downloadable file source was returned{suffix}. The author may have hidden/deleted the file, the video may still be processing, or your account may not have permission.",
                f"站点没有返回可下载文件源{suffix}。可能是作者隐藏/删除了文件、作品仍在处理，或当前账号没有权限。",
                f"保存可能なファイルソースが返されませんでした{suffix}。投稿者が非表示/削除した、処理中、または権限がない可能性があります。",
            )
        return tr(
            "No downloadable fileUrl was returned by the API.",
            "API 没有返回可下载 fileUrl。",
            "API が保存可能な fileUrl を返しませんでした。",
        )

    def _apply_task_metadata(
        self,
        task: DownloadTask,
        *,
        title: str,
        author: str,
        published_at: str,
        likes: int,
        views: int,
        slug: str,
        rating: str,
        duration: int,
        comments: int,
        tags_json: str,
        raw_json: str,
        file_url: str,
        file_id: str,
        thumbnail_index: int,
        username: str = "",
    ):
        task.title = title
        task.author = author
        task.username = username or author
        task.published_at = published_at
        task.likes = likes
        task.views = views
        task.slug = slug
        task.rating = rating
        task.duration = duration
        task.comments = comments
        task.tags_json = tags_json
        task.raw_json = raw_json
        task.file_url = file_url
        task.file_id = file_id
        task.thumbnail_index = thumbnail_index
