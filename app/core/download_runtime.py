"""Download scheduler, transfer backends, and task finalization."""
from __future__ import annotations

import gc
import json
import os
import re
import sys
import time
from datetime import datetime
from typing import Any

from ..config import app_config
from ..i18n import tr
from ..logging_setup import get_logger
from ..signal_bus import signal_bus
from .download_integrity import classify_416, incomplete_reason, resume_offset_mismatch
from .download_policy import is_time_in_window
from .manager_state import (
    CANCEL_ORIGIN_AUTO_STALL as _CANCEL_ORIGIN_AUTO_STALL,
    CANCEL_ORIGIN_MANUAL as _CANCEL_ORIGIN_MANUAL,
    EXISTING_FILE_INDEX_TTL_SECONDS as _EXISTING_FILE_INDEX_TTL_SECONDS,
    PRUNABLE_TERMINAL_STATUSES as _PRUNABLE_TERMINAL_STATUSES,
    STALL_WATCHDOG_INTERVAL_SECONDS as _STALL_WATCHDOG_INTERVAL_SECONDS,
    STALL_WATCH_STATUSES as _STALL_WATCH_STATUSES,
    TERMINAL_STATUSES as _TERMINAL_STATUSES,
)
from .models import DownloadTask, TaskStatus
from .rules import (
    active_rule_id as _default_active_rule_id,
    current_rule_payload,
    normalize_rule_payload,
    rule_store,
)
from .subscription_automation import matches_rule_metadata
from .task_metadata import (
    _author_fields_from_user,
    _compact_video_raw_json,
    _dict_or_empty,
    _subscription_download_block_from_error,
    _subscription_download_block_from_video_info,
)


def active_rule_id() -> str:
    """Resolve the manager-level hook lazily for test/integration overrides."""

    manager_module = sys.modules.get("app.core.manager")
    resolver = getattr(manager_module, "active_rule_id", _default_active_rule_id)
    return str(resolver())


logger = get_logger(__name__)


class DownloadRuntimeMixin:
    def _count_active(self) -> int:
        """Must be called with self._lock held."""
        return len(self._active_task_ids)

    def _try_activate(self):
        """Promote QUEUED_META tasks into RESOLVING up to the concurrency limit."""
        if not self._download_schedule_allows_start():
            return
        to_resolve: list[str] = []
        with self._lock:
            if self._shutting_down:
                return
            active = self._count_active()
            limit = app_config.max_concurrent
            while active < limit and self._queued_meta_ids:
                task_id = self._pop_next_queued_task_locked()
                if not task_id:
                    break
                task = self._tasks.get(task_id)
                if not task or task.status != TaskStatus.QUEUED_META:
                    continue
                task.status = TaskStatus.RESOLVING
                self._active_task_ids.add(task.task_id)
                self._touch_task_activity_locked(task.task_id)
                to_resolve.append(task.task_id)
                active += 1

        for tid in to_resolve:
            signal_bus.task_status_changed.emit(tid, TaskStatus.RESOLVING.value)
            self._resolve_executor.submit(self._resolve_task, tid)

    def _pop_next_queued_task_locked(self) -> str:
        """Pop the oldest valid task among the highest queued priority."""
        best_task_id = ""
        best_priority = -101
        stale: list[str] = []
        for task_id in self._queued_meta_ids:
            task = self._tasks.get(task_id)
            if task is None or task.status != TaskStatus.QUEUED_META:
                stale.append(task_id)
                continue
            if task.priority > best_priority:
                best_task_id = task_id
                best_priority = task.priority
        for task_id in stale:
            try:
                self._queued_meta_ids.remove(task_id)
            except ValueError:
                pass
        if best_task_id:
            self._queued_meta_ids.remove(best_task_id)
        return best_task_id

    @staticmethod
    def _download_schedule_allows_start() -> bool:
        if not app_config.download_schedule_enabled:
            return True
        return is_time_in_window(
            datetime.now().time(),
            app_config.download_schedule_start,
            app_config.download_schedule_end,
        )

    @staticmethod
    def _global_speed_limit_bytes() -> int:
        if not app_config.global_speed_limit_enabled:
            return 0
        return max(0, int(app_config.global_speed_limit_kib)) * 1024

    # ── Resolution stage ──────────────────────────────────────────────────────

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
        if self._is_cancel_requested(task_id):
            self._cancel_task_terminal(
                task_id,
                tr("Cancelled by user", "用户已中断", "ユーザーが中断しました"),
            )
            return

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
        if self._is_cancel_requested(task_id):
            self._cancel_task_terminal(
                task_id,
                tr("Cancelled by user", "用户已中断", "ユーザーが中断しました"),
            )
            return
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
            return

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
            return

        user_info = _dict_or_empty(video_info.get("user"))
        file_info = _dict_or_empty(video_info.get("file"))
        title: str = video_info.get("title", task.video_id) or task.video_id
        author, username = _author_fields_from_user(user_info)
        published_at = str(video_info.get("createdAt", "") or "")
        likes = int(video_info.get("numLikes", 0) or 0)
        views = int(video_info.get("numViews", 0) or 0)
        slug = str(video_info.get("slug", "") or "")
        rating = str(video_info.get("rating", "") or "")
        duration = int(file_info.get("duration", 0) or 0)
        comments = int(video_info.get("numComments", 0) or 0)
        raw_tags = video_info.get("tags", [])
        tags_json = json.dumps(raw_tags, ensure_ascii=False)
        raw_json = _compact_video_raw_json(video_info)
        file_url = str(video_info.get("fileUrl", "") or "")
        file_id = str(file_info.get("id", "") or "")
        thumbnail_index = int(video_info.get("thumbnail", 0) or 0)

        with self._lock:
            self._apply_task_metadata(
                task,
                title=title,
                author=author,
                username=username,
                published_at=published_at,
                likes=likes,
                views=views,
                slug=slug,
                rating=rating,
                duration=duration,
                comments=comments,
                tags_json=tags_json,
                raw_json=raw_json,
                file_url=file_url,
                file_id=file_id,
                thumbnail_index=thumbnail_index,
            )
            self._touch_task_activity_locked(task_id)

        if self._is_cancel_requested(task_id):
            self._cancel_task_terminal(
                task_id,
                tr("Cancelled by user", "用户已中断", "ユーザーが中断しました"),
            )
            return

        passed_filter, filter_reason = self._passes_filters(
            title=title,
            likes=likes,
            views=views,
            published_at=published_at,
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
            return

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

        if self._is_cancel_requested(task_id):
            self._cancel_task_terminal(
                task_id,
                tr("Cancelled by user", "用户已中断", "ユーザーが中断しました"),
            )
            return

        dl_url, quality, err2 = self._api_call(
            "get_download_info",
            video_info,
            preferred_quality=pref_quality,
            log_cb=_log,
        )
        self._touch_task_activity(task_id)
        if self._is_cancel_requested(task_id):
            self._cancel_task_terminal(
                task_id,
                tr("Cancelled by user", "用户已中断", "ユーザーが中断しました"),
            )
            return
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
            return

        self.subscriptions.update_item_download_state(task.video_id, "", "")

        signal_bus.log_message.emit(
            tr(
                f"[Resolved] \"{title}\" quality={quality}",
                f"[解析完成] 《{title}》 画质={quality}",
                f"[解析完了] 「{title}」画質={quality}",
            )
        )

        output_rel_path = self._build_output_relative_path(
            title=title,
            video_id=task.video_id,
            author=author,
            username=username,
            published_at=published_at,
            quality=quality or "",
            likes=likes,
            views=views,
            comments=comments,
            duration=duration,
            slug=slug,
            rating=rating,
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

        if self._is_cancel_requested(task_id):
            self._cancel_task_terminal(
                task_id,
                tr("Cancelled by user", "用户已中断", "ユーザーが中断しました"),
            )
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

    # ── Download stage ────────────────────────────────────────────────────────

    def _start_downloading(self, task_id: str):
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            if task.cancel_requested:
                task.status = TaskStatus.CANCELLING
                should_cancel = True
            else:
                task.status = TaskStatus.DOWNLOADING
                self._touch_task_activity_locked(task_id)
                should_cancel = False
        if should_cancel:
            self._cancel_task_terminal(
                task_id,
                tr("Cancelled by user", "用户已中断", "ユーザーが中断しました"),
            )
            return
        signal_bus.task_status_changed.emit(task_id, TaskStatus.DOWNLOADING.value)
        self._schedule_task_persist()
        self._download_executor.submit(self._download_task, task_id)

    def _download_task(self, task_id: str):
        task = self._tasks.get(task_id)
        if not task:
            return
        if self._is_cancel_requested(task_id):
            self._cancel_task_terminal(
                task_id,
                tr("Cancelled by user", "用户已中断", "ユーザーが中断しました"),
            )
            return

        # Determine save path
        final_path = task.file_path.strip() if task.file_path else ""
        if not final_path:
            fallback_rel = self._build_output_relative_path(
                title=task.title or task.video_id,
                video_id=task.video_id,
                author=task.author,
                username=task.username,
                published_at=task.published_at,
                quality=task.quality,
                likes=task.likes,
                views=task.views,
                comments=task.comments,
                duration=task.duration,
                slug=task.slug,
                rating=task.rating,
                filename_template=self._task_rule_payload(task)["filename_template"],
            )
            final_path = os.path.join(app_config.download_dir, fallback_rel)
        save_dir = os.path.dirname(final_path)
        os.makedirs(save_dir, exist_ok=True)
        temp_path = f"{final_path}_temp"

        with self._lock:
            task.file_path = final_path

        signal_bus.log_message.emit(
            tr(
                f"[Download] \"{task.title}\" [quality:{task.quality}]",
                f"[下载] 《{task.title}》 [画质:{task.quality}]",
                f"[ダウンロード] 「{task.title}」 [画質:{task.quality}]",
            )
        )
        signal_bus.log_message.emit(
            tr(
                f"  Save to: {final_path}",
                f"  保存至: {final_path}",
                f"  保存先: {final_path}",
            )
        )
        signal_bus.log_message.emit(
            tr(
                f"  Temp file: {temp_path}",
                f"  临时文件: {temp_path}",
                f"  一時ファイル: {temp_path}",
            )
        )

        if self._is_cancel_requested(task_id):
            self._cancel_task_terminal(
                task_id,
                tr("Cancelled by user", "用户已中断", "ユーザーが中断しました"),
            )
            return

        if app_config.aria2_rpc_enabled:
            self._download_task_aria2(
                task_id,
                final_path=final_path,
                temp_path=temp_path,
            )
            return

        signal_bus.log_message.emit(
            tr(
                "  aria2 disabled, using built-in downloader",
                "  aria2 未启用，使用内置下载器",
                "  aria2 無効のため内蔵ダウンローダーを使用",
            )
        )
        self._download_task_native(task_id, final_path=final_path, temp_path=temp_path)


    def _download_task_native(self, task_id: str, final_path: str, temp_path: str):
        task = self._tasks.get(task_id)
        if not task:
            return
        if self._is_cancel_requested(task_id):
            self._cancel_task_terminal(
                task_id,
                tr("Cancelled by user", "用户已中断", "ユーザーが中断しました"),
            )
            return

        resp = None
        try:
            self._touch_task_activity(task_id)
            headers: dict[str, str] = {}
            token = self._current_token()
            if token:
                headers["Authorization"] = f"Bearer {token}"

            # Resume support
            existing_size = 0
            if os.path.exists(temp_path):
                existing_size = os.path.getsize(temp_path)
                if existing_size > 0:
                    headers["Range"] = f"bytes={existing_size}-"
                    signal_bus.log_message.emit(
                        tr(
                            f"  Resume download: existing {_fmt_bytes(existing_size)}",
                            f"  断点续传: 已有 {_fmt_bytes(existing_size)}",
                            f"  レジューム: 既存 {_fmt_bytes(existing_size)}",
                        )
                    )

            resp = self.api.scraper.get(
                task.download_url,
                headers=headers,
                stream=True,
                timeout=60,
                proxies=self._download_request_proxies(),
            )
            self._touch_task_activity(task_id)
            if self._is_cancel_requested(task_id):
                self._cancel_task_terminal(
                    task_id,
                    tr("Cancelled by user", "用户已中断", "ユーザーが中断しました"),
                )
                return
            signal_bus.log_message.emit(
                f"  HTTP {resp.status_code}  Content-Length: {resp.headers.get('Content-Length', '?')}"
            )

            if resp.status_code == 416 and classify_416(resp.headers, existing_size) == "corrupt":
                self._discard_temp_file(temp_path)
                self._fail_task(
                    task_id,
                    tr(
                        "Local partial file does not match the server file; it was removed. Retry to download again.",
                        "本地缓存与服务器文件不一致，已清除；请重试以重新下载。",
                        "ローカルの一時ファイルがサーバー側と一致しないため削除しました。再試行してください。",
                    ),
                )
                return

            if resp.status_code == 416:
                signal_bus.log_message.emit(
                    tr(
                        "  File already complete, mark as completed",
                        "  文件已完整，标记完成",
                        "  ファイルは既に完全です。完了扱いにします",
                    )
                )
                if not self._finalize_temp_file(
                    task_id,
                    temp_path=temp_path,
                    final_path=final_path,
                ):
                    return
                self._complete_task(task_id)
                return

            if resp.status_code not in (200, 206):
                self._fail_task(task_id, f"HTTP {resp.status_code}: {resp.text[:200]}")
                return

            if resp.status_code == 206:
                mismatch = resume_offset_mismatch(resp.headers, existing_size)
                if mismatch:
                    self._discard_temp_file(temp_path)
                    self._fail_task(
                        task_id,
                        tr(
                            f"Resume failed ({mismatch}); partial file removed. Retry to download again.",
                            f"断点续传失败（{mismatch}），已清除缓存；请重试以重新下载。",
                            f"レジュームに失敗しました（{mismatch}）。一時ファイルを削除しました。再試行してください。",
                        ),
                    )
                    return

            # Compute total size
            content_length = int(resp.headers.get("Content-Length", 0) or 0)
            if resp.status_code == 206:
                total = existing_size + content_length
            else:
                total = content_length
                existing_size = 0  # Server ignored Range header

            with self._lock:
                task.total_bytes = total
                task.downloaded_bytes = existing_size
                self._touch_task_activity_locked(task_id)

            signal_bus.log_message.emit(
                tr(
                    f"  Total size: {_fmt_bytes(total)}",
                    f"  文件总大小: {_fmt_bytes(total)}",
                    f"  合計サイズ: {_fmt_bytes(total)}",
                )
            )

            mode = "ab" if existing_size > 0 else "wb"
            downloaded = existing_size
            last_time = time.monotonic()
            last_bytes = downloaded

            with open(temp_path, mode) as fh:
                for chunk in resp.iter_content(chunk_size=65536):
                    if self._is_cancel_requested(task_id):
                        self._cancel_task_terminal(
                            task_id,
                            tr("Cancelled by user", "用户已中断", "ユーザーが中断しました"),
                        )
                        return
                    if not chunk:
                        continue
                    if not self._rate_limiter.throttle(
                        len(chunk),
                        cancelled=lambda: self._is_cancel_requested(task_id),
                        on_wait=lambda: self._touch_task_activity(task_id),
                    ):
                        self._cancel_task_terminal(
                            task_id,
                            tr("Cancelled by user", "用户已中断", "ユーザーが中断しました"),
                        )
                        return
                    fh.write(chunk)
                    downloaded += len(chunk)
                    self._touch_task_activity(task_id)

                    now = time.monotonic()
                    if now - last_time >= 0.5:
                        elapsed = now - last_time
                        speed = (downloaded - last_bytes) / elapsed
                        speed_str = _fmt_speed(speed)
                        last_time = now
                        last_bytes = downloaded

                        with self._lock:
                            task.downloaded_bytes = downloaded
                            task.speed_str = speed_str

                        signal_bus.task_progress_updated.emit(
                            task_id, downloaded, total, speed_str
                        )

            # Final progress update
            if self._is_cancel_requested(task_id):
                self._cancel_task_terminal(
                    task_id,
                    tr("Cancelled by user", "用户已中断", "ユーザーが中断しました"),
                )
                return
            with self._lock:
                task.downloaded_bytes = downloaded
            signal_bus.task_progress_updated.emit(task_id, downloaded, total, "")

            short = incomplete_reason(downloaded, total, resp.headers)
            if short:
                # Keep the temp file: a retry resumes from what we have.
                self._fail_task(
                    task_id,
                    tr(
                        f"Incomplete download ({short}). Retry to resume.",
                        f"下载不完整（{short}），重试可继续。",
                        f"ダウンロードが不完全です（{short}）。再試行で再開できます。",
                    ),
                )
                return

            if not self._finalize_temp_file(task_id, temp_path=temp_path, final_path=final_path):
                return

            signal_bus.log_message.emit(
                tr(
                    f"[Done] \"{task.title}\" total size {_fmt_bytes(downloaded)}",
                    f"[完成] 《{task.title}》 总大小 {_fmt_bytes(downloaded)}",
                    f"[完了] 「{task.title}」 合計サイズ {_fmt_bytes(downloaded)}",
                )
            )
            self._complete_task(task_id)

        except Exception as exc:
            logger.exception("Native download failed for task %s", task_id)
            signal_bus.log_message.emit(
                tr(
                    f"[Download error] \"{task.title}\" -> {exc}",
                    f"[下载异常] 《{task.title}》 → {exc}",
                    f"[ダウンロードエラー] 「{task.title}」 -> {exc}",
                )
            )
            self._fail_task(task_id, str(exc))
        finally:
            if resp is not None:
                try:
                    resp.close()
                except Exception:
                    pass

    @staticmethod
    def _discard_temp_file(temp_path: str) -> None:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            logger.warning("Could not remove temp file %s", temp_path, exc_info=True)

    # ── Local file / filename helpers ────────────────────────────────────────

    def _find_existing_local_file(self, video_id: str) -> str | None:
        """Try to find an already-downloaded file by video ID without per-task os.walk."""
        video_id = str(video_id or "").strip()
        if not video_id:
            return None
        record = self.history.get_record(video_id)
        if record:
            file_path = str(record.get("file_path", "") or "")
            if file_path and os.path.isfile(file_path) and os.path.getsize(file_path) > 0:
                return file_path
        index = self._get_existing_file_index()
        path = index.get(video_id.lower())
        if path and os.path.isfile(path):
            return path
        return None

    def _get_existing_file_index(self) -> dict[str, str]:
        root = app_config.download_dir
        if not os.path.isdir(root):
            return {}
        now = time.monotonic()
        with self._existing_file_index_lock:
            if (
                self._existing_file_index_root == root
                and self._existing_file_index_built_at > 0
                and now - self._existing_file_index_built_at < _EXISTING_FILE_INDEX_TTL_SECONDS
            ):
                return dict(self._existing_file_index)

            index: dict[str, str] = {}
            try:
                for dirpath, _, filenames in os.walk(root):
                    for name in filenames:
                        lower_name = name.lower()
                        if lower_name.endswith("_temp") or lower_name.endswith(".aria2"):
                            continue
                        if not lower_name.endswith(".mp4"):
                            continue
                        full = os.path.join(dirpath, name)
                        if not os.path.isfile(full) or os.path.getsize(full) <= 0:
                            continue
                        for token in re.findall(r"[A-Za-z0-9_-]{8,}", os.path.splitext(name)[0]):
                            index.setdefault(token.lower(), full)
            except Exception:
                return {}
            self._existing_file_index = dict(index)
            self._existing_file_index_root = root
            self._existing_file_index_built_at = now
            return dict(index)

    # ── Terminal state helpers ────────────────────────────────────────────────

    def _forget_task_locked(self, task_id: str) -> DownloadTask | None:
        """Remove one live task and all side indexes. Must hold self._lock."""
        task = self._tasks.pop(task_id, None)
        if not task:
            return None
        self._task_id_by_video_id.pop(task.video_id.lower(), None)
        self._active_task_ids.discard(task_id)
        self._terminal_task_id_set.discard(task_id)
        self._task_last_activity.pop(task_id, None)
        return task

    def _unmark_terminal_locked(self, task_id: str):
        """Mark a previously-terminal task as live again. Must hold self._lock."""
        self._terminal_task_id_set.discard(task_id)
        self._task_last_activity.pop(task_id, None)

    def _mark_terminal_locked(self, task: DownloadTask):
        """Track terminal tasks for bounded live-memory retention."""
        self._active_task_ids.discard(task.task_id)
        self._task_last_activity.pop(task.task_id, None)
        task.download_url = ""
        task.file_url = ""
        task.raw_json = ""
        task.tags_json = ""
        task.thumbnail_url = ""
        task.speed_str = ""
        task.aria2_gid = ""
        task.file_id = ""
        self._terminal_events_since_gc += 1
        if task.task_id not in self._terminal_task_id_set:
            self._terminal_task_ids.append(task.task_id)
            self._terminal_task_id_set.add(task.task_id)

    def _prune_terminal_tasks(self) -> list[str]:
        removed: list[str] = []
        with self._lock:
            prunable_count = sum(
                1
                for task_id in self._terminal_task_id_set
                if (
                    (task := self._tasks.get(task_id))
                    and task.status in _PRUNABLE_TERMINAL_STATUSES
                )
            )
            while self._terminal_task_ids and prunable_count > self._terminal_keep_limit:
                task_id = self._terminal_task_ids.popleft()
                if task_id not in self._terminal_task_id_set:
                    continue
                task = self._tasks.get(task_id)
                if not task or task.status not in _TERMINAL_STATUSES:
                    self._terminal_task_id_set.discard(task_id)
                    continue
                if task.status not in _PRUNABLE_TERMINAL_STATUSES:
                    continue
                if self._forget_task_locked(task_id):
                    removed.append(task_id)
                    prunable_count -= 1
        if removed:
            signal_bus.tasks_removed.emit(removed)
        self._maybe_collect_garbage()
        return removed

    def _maybe_collect_garbage(self):
        should_collect = False
        now = time.monotonic()
        with self._lock:
            if self._terminal_events_since_gc >= 20 and now - self._last_gc_at >= 15:
                self._terminal_events_since_gc = 0
                self._last_gc_at = now
                should_collect = True
        if should_collect:
            gc.collect()

    def _is_cancel_requested(self, task_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            return bool(task and task.cancel_requested)

    def _restore_cancelled_task_locked(self, task: DownloadTask):
        """Reset a cancelled task for a fresh resolve. Must hold self._lock."""
        task.status = TaskStatus.QUEUED_META
        task.cancel_requested = False
        task.delete_temp_on_cancel = False
        task.remove_after_cancel = False
        task.cancel_origin = ""
        task.aria2_gid = ""
        task.error_msg = ""
        task.speed_str = ""
        task.downloaded_bytes = 0
        task.total_bytes = 0
        task.download_url = ""
        self._unmark_terminal_locked(task.task_id)
        self._queued_meta_ids.append(task.task_id)

    def _touch_task_activity(self, task_id: str):
        with self._lock:
            self._touch_task_activity_locked(task_id)

    def _touch_task_activity_locked(self, task_id: str):
        task = self._tasks.get(task_id)
        if task and task.status in _STALL_WATCH_STATUSES and not task.cancel_requested:
            self._task_last_activity[task_id] = time.monotonic()

    def _stall_watchdog_loop(self):
        while not self._watchdog_stop.wait(_STALL_WATCHDOG_INTERVAL_SECONDS):
            self._cancel_stale_tasks()
            self._restore_auto_stalled_cancelled_if_idle()

    def _cancel_stale_tasks(self) -> int:
        try:
            timeout_seconds = int(app_config.task_stall_timeout_seconds)
        except Exception:
            timeout_seconds = 30
        if timeout_seconds <= 0:
            return 0

        now = time.monotonic()
        stale: list[tuple[str, str, int, str]] = []
        with self._lock:
            for task_id in list(self._active_task_ids):
                task = self._tasks.get(task_id)
                if (
                    not task
                    or task.status not in _STALL_WATCH_STATUSES
                    or task.cancel_requested
                ):
                    continue
                last_activity = self._task_last_activity.get(task_id)
                if last_activity is None:
                    self._task_last_activity[task_id] = now
                    continue
                idle_seconds = int(now - last_activity)
                if idle_seconds < timeout_seconds:
                    continue
                stale.append(
                    (
                        task_id,
                        task.title or task.video_id,
                        idle_seconds,
                        task.aria2_gid,
                    )
                )
                task.cancel_requested = True
                task.cancel_origin = _CANCEL_ORIGIN_AUTO_STALL
                task.status = TaskStatus.CANCELLING
                task.speed_str = ""
                self._task_last_activity.pop(task_id, None)

        for task_id, title, idle_seconds, aria2_gid in stale:
            reason = tr(
                f"No activity for {timeout_seconds}s; auto-cancelled",
                f"超过 {timeout_seconds} 秒无响应，已自动中断",
                f"{timeout_seconds} 秒間応答がないため自動中断しました",
            )
            signal_bus.log_message.emit(
                tr(
                    f"[Auto-cancel] \"{title}\" idle {idle_seconds}s",
                    f"[自动中断] 《{title}》已无响应 {idle_seconds} 秒",
                    f"[自動中断] 「{title}」応答なし {idle_seconds} 秒",
                )
            )
            self._cancel_task_terminal(task_id, reason, origin=_CANCEL_ORIGIN_AUTO_STALL)
            if aria2_gid:
                self._aria2_rpc_cancel(aria2_gid)
        return len(stale)

    def _task_temp_candidates(self, task: DownloadTask) -> list[str]:
        candidates: list[str] = []
        if task.file_path:
            for suffix in ("_temp", ".tmp"):
                temp_path = f"{task.file_path}{suffix}"
                candidates.append(temp_path)
                candidates.append(f"{temp_path}.aria2")
        return list(dict.fromkeys(candidates))

    def _remove_task_temp_files(self, task: DownloadTask) -> tuple[int, int]:
        removed = 0
        failed = 0
        for temp_path in self._task_temp_candidates(task):
            if not os.path.exists(temp_path):
                continue
            try:
                os.remove(temp_path)
                removed += 1
            except Exception:
                failed += 1
        return removed, failed

    def _cancel_task_terminal(self, task_id: str, reason: str, *, origin: str = ""):
        remove_after = False
        delete_temp = False
        removed_temp = failed_temp = 0
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            if task.status in _TERMINAL_STATUSES:
                return
            task.status = TaskStatus.CANCELLED
            task.error_msg = reason
            if origin:
                task.cancel_origin = origin
            elif not task.cancel_origin:
                task.cancel_origin = _CANCEL_ORIGIN_MANUAL
            task.speed_str = ""
            task.aria2_gid = ""
            remove_after = task.remove_after_cancel
            delete_temp = task.delete_temp_on_cancel
            if not remove_after:
                self._mark_terminal_locked(task)
        if delete_temp:
            removed_temp, failed_temp = self._remove_task_temp_files(task)
            signal_bus.log_message.emit(
                tr(
                    f"[Cancelled] cleaned temp files for \"{task.title or task.video_id}\": removed {removed_temp}, failed {failed_temp}",
                    f"[已中断] 已清理《{task.title or task.video_id}》临时文件: 删除 {removed_temp} 个，失败 {failed_temp} 个",
                    f"[中断] 「{task.title or task.video_id}」一時ファイル削除: {removed_temp} / 失敗 {failed_temp}",
                )
            )

        signal_bus.task_status_changed.emit(task_id, TaskStatus.CANCELLED.value)
        signal_bus.log_message.emit(
            tr(
                f"[Cancelled] \"{task.title or task.video_id}\"",
                f"[已中断] 《{task.title or task.video_id}》",
                f"[中断] 「{task.title or task.video_id}」",
            )
        )
        if remove_after:
            with self._lock:
                self._forget_task_locked(task_id)
            signal_bus.task_removed.emit(task_id)
        else:
            self._prune_terminal_tasks()
        self._schedule_task_persist()
        self._try_activate()

    def _fail_task(self, task_id: str, reason: str):
        cancel_requested = False
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            cancel_requested = task.cancel_requested
            if cancel_requested:
                task.status = TaskStatus.CANCELLING
            else:
                task.status = TaskStatus.FAILED
                task.error_msg = reason
                self._mark_terminal_locked(task)
        if cancel_requested:
            self._cancel_task_terminal(
                task_id,
                tr("Cancelled by user", "用户已中断", "ユーザーが中断しました"),
            )
            return
        signal_bus.task_status_changed.emit(task_id, TaskStatus.FAILED.value)
        signal_bus.task_error.emit(task_id, reason)
        self._prune_terminal_tasks()
        self._schedule_task_persist()
        # Free concurrency slot
        self._try_activate()

    def _finalize_temp_file(self, task_id: str, temp_path: str, final_path: str) -> bool:
        if self._is_cancel_requested(task_id):
            self._cancel_task_terminal(
                task_id,
                tr("Cancelled by user", "用户已中断", "ユーザーが中断しました"),
            )
            return False
        if not os.path.exists(temp_path):
            self._fail_task(
                task_id,
                tr(
                    "Temp file does not exist, cannot finalize",
                    "临时文件不存在，无法完成重命名",
                    "一時ファイルが存在しないため確定できません",
                ),
            )
            return False
        size = os.path.getsize(temp_path)
        if size <= 0:
            self._fail_task(
                task_id,
                tr(
                    "Temp file is empty, download is incomplete",
                    "临时文件为空，下载不完整",
                    "一時ファイルが空のためダウンロードが不完全です",
                ),
            )
            return False
        try:
            os.replace(temp_path, final_path)
            sidecar = f"{temp_path}.aria2"
            if os.path.exists(sidecar):
                os.remove(sidecar)
            return True
        except Exception as exc:
            self._fail_task(
                task_id,
                tr(
                    f"Failed to rename temp file: {exc}",
                    f"重命名临时文件失败: {exc}",
                    f"一時ファイルのリネームに失敗: {exc}",
                ),
            )
            return False

    def _log_retry_temp_cleanup(self, title: str, file_path: str) -> tuple[int, int]:
        removed, failed = self._clear_retry_temp_files(file_path)
        if not removed and not failed:
            return removed, failed
        signal_bus.log_message.emit(
            tr(
                f"[Retry] Cleaned temp cache for \"{title}\": removed {removed}, failed {failed}",
                f"[重试] 已清理《{title}》对应临时缓存: 删除 {removed} 个，失败 {failed} 个",
                f"[再試行] 「{title}」の一時キャッシュを削除: 削除 {removed} / 失敗 {failed}",
            )
        )
        return removed, failed

    @staticmethod
    def _clear_retry_temp_files(file_path: str) -> tuple[int, int]:
        if not file_path:
            return 0, 0
        candidates: list[str] = []
        for suffix in ("_temp", ".tmp"):
            temp_path = f"{file_path}{suffix}"
            candidates.append(temp_path)
            candidates.append(f"{temp_path}.aria2")

        removed = 0
        failed = 0
        for temp_path in dict.fromkeys(candidates):
            if not os.path.isfile(temp_path):
                continue
            try:
                os.remove(temp_path)
                removed += 1
            except Exception:
                failed += 1
        return removed, failed

    def clear_temp_files(self) -> tuple[int, int]:
        """Delete all *_temp files under download directory.

        Returns:
            (removed_count, failed_count)
        """
        root = app_config.download_dir
        if not os.path.isdir(root):
            return 0, 0

        removed = 0
        failed = 0
        for dirpath, _, filenames in os.walk(root):
            for name in filenames:
                if not name.endswith("_temp"):
                    continue
                temp_file = os.path.join(dirpath, name)
                try:
                    os.remove(temp_file)
                    removed += 1
                except Exception:
                    failed += 1
                    continue
                sidecar = f"{temp_file}.aria2"
                if os.path.exists(sidecar):
                    try:
                        os.remove(sidecar)
                    except Exception:
                        failed += 1
        return removed, failed

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

    @staticmethod
    def _normalize_rule_id(rule_id: str | None) -> str:
        return str(rule_id or active_rule_id()).strip() or active_rule_id()

    def _rule_payload_for_id(self, rule_id: str | None = "") -> dict[str, Any]:
        selected_id = str(rule_id or "").strip()
        if selected_id:
            rule = rule_store.find(selected_id)
            if rule:
                return normalize_rule_payload(rule.get("payload"))
        return normalize_rule_payload(current_rule_payload())

    def _task_rule_payload(self, task: DownloadTask) -> dict[str, Any]:
        raw_snapshot = str(getattr(task, "rule_payload_json", "") or "").strip()
        if raw_snapshot:
            try:
                snapshot = json.loads(raw_snapshot)
            except (TypeError, ValueError, json.JSONDecodeError):
                snapshot = None
            if isinstance(snapshot, dict):
                return normalize_rule_payload(snapshot)
        return self._rule_payload_for_id(task.rule_id)

    def _passes_filters(
        self,
        title: str,
        likes: int,
        views: int,
        published_at: str,
        tags: list[Any],
        payload: dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        return matches_rule_metadata(
            title=title,
            likes=likes,
            views=views,
            published_at=published_at,
            tags=tags,
            payload=payload or current_rule_payload(),
        )


    def _skip_task(self, task_id: str, reason: str):
        if self._is_cancel_requested(task_id):
            self._cancel_task_terminal(
                task_id,
                tr("Cancelled by user", "用户已中断", "ユーザーが中断しました"),
            )
            return
        with self._lock:
            task = self._tasks.get(task_id)
            if task:
                task.status = TaskStatus.SKIPPED
                task.error_msg = reason
                self._mark_terminal_locked(task)
        signal_bus.log_message.emit(
            tr(
                f"[Filtered] {task_id} -> {reason}",
                f"[筛选跳过] {task_id} → {reason}",
                f"[フィルター] {task_id} -> {reason}",
            )
        )
        signal_bus.task_status_changed.emit(task_id, TaskStatus.SKIPPED.value)
        self._prune_terminal_tasks()
        self._schedule_task_persist()
        self._try_activate()


    def _complete_task(self, task_id: str):
        task = self._tasks.get(task_id)
        if not task:
            return
        if self._is_cancel_requested(task_id):
            self._cancel_task_terminal(
                task_id,
                tr("Cancelled by user", "用户已中断", "ユーザーが中断しました"),
            )
            return
        with self._lock:
            task.status = TaskStatus.COMPLETED
        rule_payload = self._task_rule_payload(task)
        if rule_payload["download_thumbnail"]:
            self._download_thumbnail(task)
        if rule_payload["collect_nfo_info"]:
            self._write_nfo(task)
        if rule_payload["record_to_history"]:
            try:
                self.history.upsert_downloaded(
                    {
                        "video_id": task.video_id,
                        "title": task.title,
                        "author": task.author,
                        "published_at": task.published_at,
                        "likes": task.likes,
                        "views": task.views,
                        "slug": task.slug,
                        "rating": task.rating,
                        "duration": task.duration,
                        "comments": task.comments,
                        "tags_json": task.tags_json,
                        "raw_json": task.raw_json,
                        "source_url": task.url,
                        "file_path": task.file_path,
                        "thumbnail_path": task.thumbnail_path,
                        "quality": task.quality,
                    }
                )
            except Exception as exc:
                logger.exception("Failed to write history DB for task %s", task_id)
                signal_bus.log_message.emit(
                    tr(
                        f"[Warning] Failed to write history DB (download file is safe): {exc}",
                        f"[警告] 写入历史库失败（不影响文件下载）: {exc}",
                        f"[警告] 履歴DB書き込み失敗（ダウンロードファイルには影響なし）: {exc}",
                    )
                )
        else:
            signal_bus.log_message.emit(
                tr(
                    f"[History] skipped for \"{task.title}\" by rule choice",
                    f"[历史] 按规则选择跳过《{task.title}》的历史记录",
                    f"[履歴] ルール設定により「{task.title}」を履歴へ記録しません",
                )
            )
        with self._lock:
            live_task = self._tasks.get(task_id)
            if live_task:
                with self._existing_file_index_lock:
                    self._existing_file_index[live_task.video_id.lower()] = live_task.file_path
                self._mark_terminal_locked(live_task)
        signal_bus.task_status_changed.emit(task_id, TaskStatus.COMPLETED.value)
        self._prune_terminal_tasks()
        self._schedule_task_persist()
        # Free concurrency slot
        self._try_activate()


def _fmt_speed(bps: float) -> str:
    if bps >= 1024**2:
        return f"{bps / 1024**2:.1f} MB/s"
    if bps >= 1024:
        return f"{bps / 1024:.1f} KB/s"
    return f"{bps:.0f} B/s"


def format_speed(bps: float) -> str:
    """Public name for the speed text shown in the task list."""

    return _fmt_speed(max(0.0, float(bps or 0)))


def parse_speed(text: str) -> float:
    """Bytes per second for text produced by ``_fmt_speed`` (``"1.5 MB/s"``); 0 if blank."""

    parts = str(text or "").strip().split()
    if len(parts) != 2:
        return 0.0
    unit = {"B/S": 1.0, "KB/S": 1024.0, "MB/S": 1024.0**2, "GB/S": 1024.0**3}.get(parts[1].upper())
    if unit is None:
        return 0.0
    try:
        return max(0.0, float(parts[0])) * unit
    except ValueError:
        return 0.0


def _fmt_bytes(n: int) -> str:
    if n >= 1024**3:
        return f"{n / 1024**3:.1f} GB"
    if n >= 1024**2:
        return f"{n / 1024**2:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n} B"
