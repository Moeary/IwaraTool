"""Download manager: state-machine scheduler + thread pool executor.

State machine flow
──────────────────
QUEUED_META → RESOLVING → QUEUED_DOWNLOAD → DOWNLOADING → COMPLETED
                                                        ↘ FAILED
                        RESOLVING ─────────────────────→ SKIPPED (filtered)

Concurrency rule
────────────────
At any moment:  len(RESOLVING) + len(QUEUED_DOWNLOAD) + len(DOWNLOADING) ≤ max_concurrent

This ensures download URLs are never resolved too early and expire before use.
"""

from __future__ import annotations

import os
import json
import re
import shutil
import subprocess
import threading
import time
import uuid
from xml.sax.saxutils import escape
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlparse

from ..config import app_config
from ..i18n import tr
from ..signal_bus import signal_bus
from .api import IwaraAPI
from .history import DownloadHistory
from .models import DownloadTask, TaskStatus

if TYPE_CHECKING:
    pass


_ACTIVE_STATUSES = frozenset(
    [TaskStatus.RESOLVING, TaskStatus.QUEUED_DOWNLOAD, TaskStatus.DOWNLOADING]
)


class DownloadManager:
    """Central manager for all download tasks.

    Thread-safe: all internal state mutations are protected by self._lock.
    Qt signals are emitted *outside* the lock to avoid deadlocks.
    """

    def __init__(self):
        self.api = IwaraAPI()
        self.history = DownloadHistory()

        # task_id → DownloadTask
        self._tasks: dict[str, DownloadTask] = {}
        self._lock = threading.Lock()

        # Large pool: some threads will be idle most of the time
        self._executor = ThreadPoolExecutor(max_workers=16, thread_name_prefix="iwara")

    # ── Public API ────────────────────────────────────────────────────────────

    def get_tasks(self) -> list[DownloadTask]:
        with self._lock:
            return list(self._tasks.values())

    def add_url(self, url: str):
        """Parse URL and enqueue tasks (runs in background thread)."""
        self._executor.submit(self._parse_and_enqueue, url)

    def retry_task(self, task_id: str):
        """Re-queue a failed task."""
        with self._lock:
            task = self._tasks.get(task_id)
            if task and task.status == TaskStatus.FAILED:
                task.status = TaskStatus.QUEUED_META
                task.error_msg = ""
                task.downloaded_bytes = 0
                task.total_bytes = 0
                task.download_url = ""
        signal_bus.task_status_changed.emit(task_id, TaskStatus.QUEUED_META.value)
        self._try_activate()

    def retry_all_failed(self, exclude_downloaded: bool = True) -> tuple[int, int]:
        """Retry all failed tasks.

        Args:
            exclude_downloaded: if True, failed tasks that already have local
                completed files are marked completed instead of retried.

        Returns:
            (retried_count, skipped_as_completed_count)
        """
        to_retry: list[str] = []
        to_complete: list[str] = []

        with self._lock:
            failed_tasks = [
                t for t in self._tasks.values() if t.status == TaskStatus.FAILED
            ]

            for task in failed_tasks:
                if exclude_downloaded and self._find_existing_local_file(task.video_id):
                    to_complete.append(task.task_id)
                    continue

                task.status = TaskStatus.QUEUED_META
                task.error_msg = ""
                task.downloaded_bytes = 0
                task.total_bytes = 0
                task.download_url = ""
                to_retry.append(task.task_id)

        for tid in to_complete:
            self._complete_task(tid)
        for tid in to_retry:
            signal_bus.task_status_changed.emit(tid, TaskStatus.QUEUED_META.value)

        self._try_activate()
        return len(to_retry), len(to_complete)

    def remove_task(self, task_id: str):
        with self._lock:
            removed = self._tasks.pop(task_id, None)
        if removed:
            signal_bus.task_removed.emit(task_id)

    def clear_completed(self):
        """Remove terminal tasks from UI board (completed/skipped/failed)."""
        removed_ids: list[str] = []
        with self._lock:
            to_remove = [
                tid
                for tid, t in self._tasks.items()
                if t.status in (TaskStatus.COMPLETED, TaskStatus.SKIPPED, TaskStatus.FAILED)
            ]
            for tid in to_remove:
                del self._tasks[tid]
                removed_ids.append(tid)
        for tid in removed_ids:
            signal_bus.task_removed.emit(tid)

    def open_task_output(self, task_id: str) -> tuple[bool, str]:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return False, tr("Task does not exist", "任务不存在", "タスクが存在しません")
            status = task.status
            file_path = task.file_path
            title = task.title or task.video_id

        if status != TaskStatus.COMPLETED:
            return False, tr(
                "Only completed tasks can be opened",
                "仅支持已完成任务",
                "完了タスクのみ開けます",
            )
        if not file_path or not os.path.exists(file_path):
            return False, tr("File does not exist", "文件不存在", "ファイルが存在しません")

        action = str(app_config.completed_task_click_action or "folder").lower()
        target = file_path if action == "player" else os.path.dirname(file_path)
        if not target:
            return False, tr("No openable path", "无可打开路径", "開けるパスがありません")

        try:
            if os.name == "nt":
                os.startfile(target)
            elif shutil.which("xdg-open"):
                subprocess.Popen(["xdg-open", target])
            elif shutil.which("open"):
                subprocess.Popen(["open", target])
            else:
                return False, tr(
                    "System does not support auto-open",
                    "系统不支持自动打开",
                    "システムが自動オープンに対応していません",
                )
        except Exception as exc:
            return False, str(exc)

        action_text = tr("Player", "播放器", "プレイヤー") if action == "player" else tr("Folder", "文件夹", "フォルダー")
        signal_bus.log_message.emit(
            tr(
                f"[Open] \"{title}\" -> {action_text}",
                f"[打开] 《{title}》 → {action_text}",
                f"[開く] 「{title}」 -> {action_text}",
            )
        )
        return True, ""

    def set_login(self, logged_in: bool, token: str | None = None):
        if logged_in and token:
            self.api.token = token
            app_config.auth_token = token
            app_config.auth_token_saved_at = datetime.now().isoformat(timespec="seconds")
        elif not logged_in:
            self.api.token = None
            app_config.auth_token = ""
            app_config.auth_token_saved_at = ""

    def restore_cached_login(self) -> bool:
        token = (app_config.auth_token or "").strip()
        if not (app_config.auth_enabled and token):
            return False
        self.api.token = token
        return True

    def apply_config(self):
        """Apply proxy settings from app_config to the scraper."""
        if app_config.proxy_enabled and app_config.proxy_url:
            self.api.set_proxy(app_config.proxy_url)
        else:
            self.api.set_proxy("")

    # ── URL parsing ───────────────────────────────────────────────────────────

    def _parse_and_enqueue(self, raw: str):
        url = raw.strip()
        if not url:
            return
        signal_bus.log_message.emit(
            tr(f"Parsing input: {url}", f"解析输入：{url}", f"入力を解析中: {url}")
        )

        parsed = self._parse_iwara_url(url)
        if parsed:
            kind, value = parsed
            if kind == "video":
                signal_bus.log_message.emit(
                    tr(
                        f"[Detected] Video URL -> video_id={value}",
                        f"[识别] 视频链接 → video_id={value}",
                        f"[検出] 動画URL -> video_id={value}",
                    )
                )
            elif kind == "user":
                signal_bus.log_message.emit(
                    tr(
                        f"[Detected] User URL -> username={value}",
                        f"[识别] 用户链接 → username={value}",
                        f"[検出] ユーザーURL -> username={value}",
                    )
                )
            elif kind == "playlist":
                signal_bus.log_message.emit(
                    tr(
                        f"[Detected] Playlist URL -> playlist_id={value}",
                        f"[识别] 播放列表链接 → playlist_id={value}",
                        f"[検出] プレイリストURL -> playlist_id={value}",
                    )
                )
            elif kind == "search":
                signal_bus.log_message.emit(
                    tr(
                        "[Detected] API search URL",
                        "[识别] API 搜索链接",
                        "[検出] API 検索URL",
                    )
                )
            if kind == "video":
                self._enqueue_video_id(value, url)
                return
            if kind == "user":
                self._enqueue_user(value)
                return
            if kind == "playlist":
                self._enqueue_playlist(value)
                return
            if kind == "search":
                self._enqueue_search_query(value)
                return

        # Treat as raw video ID
        signal_bus.log_message.emit(
            tr(
                f"[Detected] Treat as raw video ID -> {url}",
                f"[识别] 按视频ID处理 → {url}",
                f"[検出] 生の動画IDとして処理 -> {url}",
            )
        )
        self._enqueue_video_id(url, url)

    def _parse_iwara_url(
        self, raw_url: str
    ) -> tuple[str, str | dict[str, str]] | None:
        """Parse iwara URLs into ('video'|'user'|'playlist'|'search', value)."""
        normalized = raw_url.strip()
        if "iwara.tv" in normalized and "://" not in normalized:
            normalized = f"https://{normalized.lstrip('/')}"

        try:
            parsed = urlparse(normalized)
        except Exception:
            return None

        host = (parsed.netloc or "").lower()
        if host and "iwara.tv" not in host:
            return None

        parts = [p for p in parsed.path.split("/") if p]
        if not parts:
            return None
        lower_parts = [p.lower() for p in parts]

        # Search endpoint, e.g.
        # - https://api.iwara.tv/videos?tags=2d&sort=date
        # - https://www.iwara.tv/videos?tags=2d&sort=date
        if lower_parts[0] == "videos":
            query_raw = parse_qs(parsed.query, keep_blank_values=False)
            query_params = {
                key: values[-1].strip()
                for key, values in query_raw.items()
                if values and values[-1].strip()
            }
            # Keep behavior sane for bare /videos links.
            if not query_params:
                query_params["sort"] = "date"
            return "search", query_params

        def _next_after(key: str) -> str | None:
            try:
                idx = lower_parts.index(key)
            except ValueError:
                return None
            if idx + 1 >= len(parts):
                return None
            return parts[idx + 1]

        video_id = _next_after("video")
        if video_id:
            return "video", video_id

        playlist_id = _next_after("playlist")
        if playlist_id:
            return "playlist", playlist_id

        username = _next_after("user") or _next_after("profile")
        if username:
            return "user", username

        return None

    def _enqueue_video_id(self, video_id: str, original_url: str):
        # Only local files are authoritative for skip-existing checks.
        if app_config.skip_existing_files:
            existing_path = self._find_existing_local_file(video_id)
            if existing_path:
                signal_bus.log_message.emit(
                    tr(
                        f"[Skipped] {video_id} local file already exists: {existing_path}",
                        f"[跳过] {video_id} 已存在本地文件: {existing_path}",
                        f"[スキップ] {video_id} はローカルに既存: {existing_path}",
                    )
                )
                return

        with self._lock:
            # Dedup: skip if already in current queue
            for t in self._tasks.values():
                if t.video_id == video_id:
                    signal_bus.log_message.emit(
                        tr(
                            f"[Skipped] {video_id} already in queue",
                            f"[跳过] {video_id} 已在队列中",
                            f"[スキップ] {video_id} は既にキュー内です",
                        )
                    )
                    return
            task_id = str(uuid.uuid4())
            task = DownloadTask(task_id=task_id, url=original_url, video_id=video_id)
            self._tasks[task_id] = task

            queued_count = sum(
                1 for t in self._tasks.values() if t.status == TaskStatus.QUEUED_META
            )
            active_count = self._count_active()
            limit = app_config.max_concurrent

        signal_bus.log_message.emit(
            tr(
                f"[Queued] {video_id} (queued: {queued_count}, active: {active_count}/{limit})",
                f"[入队] {video_id}（当前排队: {queued_count}，活动: {active_count}/{limit}）",
                f"[キュー追加] {video_id}（待機: {queued_count}、稼働: {active_count}/{limit}）",
            )
        )

        signal_bus.task_added.emit(
            task_id,
            {
                "video_id": video_id,
                "title": video_id,
                "author": "",
                "status": TaskStatus.QUEUED_META.value,
            },
        )
        self._try_activate()

    def _enqueue_user(self, username: str):
        signal_bus.log_message.emit(
            tr(
                f"Fetching videos for user [{username}] ...",
                f"正在获取用户 [{username}] 的视频列表…",
                f"ユーザー [{username}] の動画一覧を取得中...",
            )
        )
        user_id, err = self.api.get_user_id(username)
        if not user_id:
            signal_bus.log_message.emit(
                tr(
                    f"[Error] Failed to get user ID: {err}",
                    f"[错误] 无法获取用户 ID: {err}",
                    f"[エラー] ユーザーIDの取得に失敗: {err}",
                )
            )
            return
        videos = self.api.get_user_videos(user_id)
        signal_bus.log_message.emit(
            tr(
                f"Found {len(videos)} videos for user [{username}]",
                f"用户 [{username}] 共找到 {len(videos)} 个视频",
                f"ユーザー [{username}] で {len(videos)} 件の動画を検出",
            )
        )
        for video in videos:
            vid = video.get("id", "")
            if vid:
                self._enqueue_video_id(vid, f"https://www.iwara.tv/video/{vid}")

    def _enqueue_playlist(self, playlist_id: str):
        signal_bus.log_message.emit(
            tr(
                f"Fetching videos from playlist [{playlist_id}] ...",
                f"正在获取播放列表 [{playlist_id}] 的视频…",
                f"プレイリスト [{playlist_id}] の動画を取得中...",
            )
        )
        videos = self.api.get_playlist_videos(playlist_id)
        signal_bus.log_message.emit(
            tr(
                f"Found {len(videos)} videos in playlist",
                f"播放列表共找到 {len(videos)} 个视频",
                f"プレイリストで {len(videos)} 件の動画を検出",
            )
        )
        for video in videos:
            vid = video.get("id", "")
            if vid:
                self._enqueue_video_id(vid, f"https://www.iwara.tv/video/{vid}")

    def _enqueue_search_query(self, query_params: dict[str, str]):
        if not query_params:
            signal_bus.log_message.emit(
                tr(
                    "[Error] Empty API query parameters",
                    "[错误] API 搜索参数为空",
                    "[エラー] API 検索パラメータが空です",
                )
            )
            return

        configured_cap = (
            max(0, int(app_config.search_limit_count))
            if app_config.search_limit_enabled
            else 0
        )
        query_text = "&".join(f"{k}={v}" for k, v in query_params.items())
        if configured_cap > 0:
            signal_bus.log_message.emit(
                tr(
                    f"Fetching search videos: {query_text} (cap: {configured_cap})",
                    f"正在拉取搜索结果: {query_text}（上限: {configured_cap}）",
                    f"検索結果を取得中: {query_text}（上限: {configured_cap}）",
                )
            )
        else:
            signal_bus.log_message.emit(
                tr(
                    f"Fetching search videos: {query_text}",
                    f"正在拉取搜索结果: {query_text}",
                    f"検索結果を取得中: {query_text}",
                )
            )

        videos, err = self.api.get_videos_by_query(
            query_params,
            max_results=configured_cap,
        )
        if err:
            signal_bus.log_message.emit(
                tr(
                    f"[Warning] Search API returned partial result: {err}",
                    f"[警告] 搜索 API 返回部分结果: {err}",
                    f"[警告] 検索 API は部分結果を返しました: {err}",
                )
            )
        if not videos:
            signal_bus.log_message.emit(
                tr(
                    "No videos found for this query",
                    "该查询未找到视频",
                    "このクエリでは動画が見つかりませんでした",
                )
            )
            return

        signal_bus.log_message.emit(
            tr(
                f"Search found {len(videos)} videos",
                f"搜索共找到 {len(videos)} 个视频",
                f"検索で {len(videos)} 件の動画を検出",
            )
        )
        for video in videos:
            vid = str(video.get("id", "") or "").strip()
            if vid:
                self._enqueue_video_id(vid, f"https://www.iwara.tv/video/{vid}")

    # ── Scheduler ─────────────────────────────────────────────────────────────

    def _count_active(self) -> int:
        """Must be called with self._lock held."""
        return sum(1 for t in self._tasks.values() if t.status in _ACTIVE_STATUSES)

    def _try_activate(self):
        """Promote QUEUED_META tasks into RESOLVING up to the concurrency limit."""
        to_resolve: list[str] = []
        with self._lock:
            active = self._count_active()
            limit = app_config.max_concurrent
            queued = [
                t for t in self._tasks.values() if t.status == TaskStatus.QUEUED_META
            ]
            while active < limit and queued:
                task = queued.pop(0)
                task.status = TaskStatus.RESOLVING
                to_resolve.append(task.task_id)
                active += 1

        for tid in to_resolve:
            signal_bus.task_status_changed.emit(tid, TaskStatus.RESOLVING.value)
            self._executor.submit(self._resolve_task, tid)

    # ── Resolution stage ──────────────────────────────────────────────────────

    def _resolve_task(self, task_id: str):
        task = self._tasks.get(task_id)
        if not task:
            return

        signal_bus.log_message.emit(
            tr(
                f"[Resolve] Fetching video info: {task.video_id}",
                f"[解析] 开始获取视频信息: {task.video_id}",
                f"[解析] 動画情報を取得中: {task.video_id}",
            )
        )

        video_info, err = self.api.get_video_info(task.video_id)
        if not video_info:
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

        # Re-try with login if private
        if (
            not video_info.get("fileUrl")
            and video_info.get("message") == "errors.privateVideo"
        ):
            if not self.api.token:
                self._fail_task(
                    task_id,
                    tr(
                        "Private video. Please login and try again.",
                        "私有视频，请先登录后重试",
                        "非公開動画です。ログインして再試行してください。",
                    ),
                )
                signal_bus.log_message.emit(
                    tr(
                        f"[Skipped] {task.video_id} is private, login required",
                        f"[跳过] {task.video_id} 为私有视频，请先登录",
                        f"[スキップ] {task.video_id} は非公開です。ログインが必要です",
                    )
                )
                return
            self._fail_task(
                task_id,
                tr(
                    "Private video (logged in but no permission)",
                    "私有视频（已登录但无权限）",
                    "非公開動画（ログイン済みですが権限がありません）",
                ),
            )
            signal_bus.log_message.emit(
                tr(
                    f"[Skipped] {task.video_id} private, no permission",
                    f"[跳过] {task.video_id} 私有视频，无权限",
                    f"[スキップ] {task.video_id} 非公開・権限なし",
                )
            )
            return

        title: str = video_info.get("title", task.video_id) or task.video_id
        author: str = video_info.get("user", {}).get("username", "") or ""
        published_at = str(video_info.get("createdAt", "") or "")
        likes = int(video_info.get("numLikes", 0) or 0)
        views = int(video_info.get("numViews", 0) or 0)
        slug = str(video_info.get("slug", "") or "")
        rating = str(video_info.get("rating", "") or "")
        duration = int(video_info.get("file", {}).get("duration", 0) or 0)
        comments = int(video_info.get("numComments", 0) or 0)
        raw_tags = video_info.get("tags", [])
        tags_json = json.dumps(raw_tags, ensure_ascii=False)
        raw_json = json.dumps(video_info, ensure_ascii=False)
        file_url = str(video_info.get("fileUrl", "") or "")
        file_id = str(video_info.get("file", {}).get("id", "") or "")
        thumbnail_index = int(video_info.get("thumbnail", 0) or 0)

        with self._lock:
            self._apply_task_metadata(
                task,
                title=title,
                author=author,
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

        passed_filter, filter_reason = self._passes_filters(
            likes=likes,
            views=views,
            published_at=published_at,
            tags=raw_tags if isinstance(raw_tags, list) else [],
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
            signal_bus.log_message.emit(msg)

        dl_url, quality, err2 = self.api.get_download_info(
            video_info,
            preferred_quality=pref_quality,
            log_cb=_log,
        )
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
            published_at=published_at,
            quality=quality or "",
            likes=likes,
            views=views,
            comments=comments,
            duration=duration,
            slug=slug,
            rating=rating,
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
            task.status = TaskStatus.QUEUED_DOWNLOAD

        signal_bus.task_status_changed.emit(task_id, TaskStatus.QUEUED_DOWNLOAD.value)
        # Immediately transition to download — slot is already counted
        self._start_downloading(task_id)

    # ── Download stage ────────────────────────────────────────────────────────

    def _start_downloading(self, task_id: str):
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            task.status = TaskStatus.DOWNLOADING
        signal_bus.task_status_changed.emit(task_id, TaskStatus.DOWNLOADING.value)
        self._executor.submit(self._download_task, task_id)

    def _download_task(self, task_id: str):
        task = self._tasks.get(task_id)
        if not task:
            return

        # Determine save path
        final_path = task.file_path.strip() if task.file_path else ""
        if not final_path:
            fallback_rel = self._build_output_relative_path(
                title=task.title or task.video_id,
                video_id=task.video_id,
                author=task.author,
                published_at=task.published_at,
                quality=task.quality,
                likes=task.likes,
                views=task.views,
                comments=task.comments,
                duration=task.duration,
                slug=task.slug,
                rating=task.rating,
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

    def _download_task_aria2(
        self,
        task_id: str,
        final_path: str,
        temp_path: str,
    ):
        task = self._tasks.get(task_id)
        if not task:
            return

        rpc_url = app_config.aria2_rpc_url.strip()
        if not rpc_url:
            signal_bus.log_message.emit(
                tr(
                    "  aria2 RPC URL is empty, fallback to built-in downloader",
                    "  aria2 RPC 地址为空，回退到内置下载器",
                    "  aria2 RPC URL が空のため内蔵ダウンローダーへフォールバック",
                )
            )
            self._download_task_native(task_id, final_path=final_path, temp_path=temp_path)
            return

        save_dir = os.path.dirname(temp_path)
        filename = os.path.basename(temp_path)
        headers: list[str] = []
        if self.api.token:
            headers.append(f"Authorization: Bearer {self.api.token}")

        options: dict[str, str | list[str]] = {
            "dir": save_dir,
            "out": filename,
            "continue": "true",
            "max-connection-per-server": "16",
            "split": "16",
            "min-split-size": "1M",
            "timeout": "60",
            "max-tries": "5",
            "retry-wait": "2",
            "auto-file-renaming": "false",
            "allow-overwrite": "false",
            "file-allocation": "none",
        }
        if headers:
            options["header"] = headers
        if app_config.proxy_enabled and app_config.proxy_url:
            options["all-proxy"] = app_config.proxy_url

        signal_bus.log_message.emit(
            tr(
                f"  Download via aria2 RPC: {rpc_url}",
                f"  使用 aria2 RPC 下载: {rpc_url}",
                f"  aria2 RPC でダウンロード: {rpc_url}",
            )
        )
        gid, add_err = self._aria2_rpc_add_uri(task.download_url, options)
        if not gid:
            signal_bus.log_message.emit(
                tr(
                    f"  aria2 RPC submit failed, fallback to built-in downloader: {add_err}",
                    f"  aria2 RPC 提交失败，回退到内置下载器: {add_err}",
                    f"  aria2 RPC 送信失敗、内蔵ダウンローダーへフォールバック: {add_err}",
                )
            )
            self._download_task_native(task_id, final_path=final_path, temp_path=temp_path)
            return

        last_emit = 0.0
        while True:
            status_info, err = self._aria2_rpc_tell_status(gid)
            if not status_info:
                self._fail_task(
                    task_id,
                    tr(
                        f"aria2 RPC query failed: {err}",
                        f"aria2 RPC 查询失败: {err}",
                        f"aria2 RPC 問い合わせ失敗: {err}",
                    ),
                )
                return

            status = str(status_info.get("status", ""))
            done = int(status_info.get("completedLength", "0") or 0)
            total = int(status_info.get("totalLength", "0") or 0)
            speed = int(status_info.get("downloadSpeed", "0") or 0)
            speed_str = _fmt_speed(float(speed)) if speed > 0 else ""

            now = time.monotonic()
            if now - last_emit >= 0.5:
                with self._lock:
                    task.downloaded_bytes = done
                    task.total_bytes = total
                    task.speed_str = speed_str
                signal_bus.task_progress_updated.emit(task_id, done, total, speed_str)
                last_emit = now

            if status == "complete":
                downloaded = os.path.getsize(temp_path) if os.path.exists(temp_path) else done
                if not self._finalize_temp_file(task_id, temp_path=temp_path, final_path=final_path):
                    return
                with self._lock:
                    task.downloaded_bytes = downloaded
                    task.total_bytes = max(total, downloaded)
                    task.speed_str = ""
                signal_bus.task_progress_updated.emit(task_id, downloaded, max(total, downloaded), "")
                signal_bus.log_message.emit(
                    tr(
                        f"[Done] \"{task.title}\" total size {_fmt_bytes(downloaded)}",
                        f"[完成] 《{task.title}》 总大小 {_fmt_bytes(downloaded)}",
                        f"[完了] 「{task.title}」 合計サイズ {_fmt_bytes(downloaded)}",
                    )
                )
                self._complete_task(task_id)
                self._aria2_rpc_remove_result(gid)
                return

            if status in ("error", "removed"):
                err_msg = str(
                    status_info.get(
                        "errorMessage",
                        tr("aria2 unknown error", "aria2 未知错误", "aria2 不明エラー"),
                    )
                    or tr("aria2 unknown error", "aria2 未知错误", "aria2 不明エラー")
                )
                self._fail_task(task_id, f"aria2 {status}: {err_msg}")
                self._aria2_rpc_remove_result(gid)
                return

            time.sleep(0.5)

    def _download_task_native(self, task_id: str, final_path: str, temp_path: str):
        task = self._tasks.get(task_id)
        if not task:
            return

        try:
            headers: dict[str, str] = {}
            if self.api.token:
                headers["Authorization"] = f"Bearer {self.api.token}"

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
                task.download_url, headers=headers, stream=True, timeout=60
            )
            signal_bus.log_message.emit(
                f"  HTTP {resp.status_code}  Content-Length: {resp.headers.get('Content-Length', '?')}"
            )

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

            # Compute total size
            content_length = int(resp.headers.get("Content-Length", 0))
            if resp.status_code == 206:
                total = existing_size + content_length
            else:
                total = content_length
                existing_size = 0  # Server ignored Range header

            with self._lock:
                task.total_bytes = total
                task.downloaded_bytes = existing_size

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
                    if not chunk:
                        continue
                    fh.write(chunk)
                    downloaded += len(chunk)

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
            with self._lock:
                task.downloaded_bytes = downloaded
            signal_bus.task_progress_updated.emit(task_id, downloaded, total, "")

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
            signal_bus.log_message.emit(
                tr(
                    f"[Download error] \"{task.title}\" -> {exc}",
                    f"[下载异常] 《{task.title}》 → {exc}",
                    f"[ダウンロードエラー] 「{task.title}」 -> {exc}",
                )
            )
            self._fail_task(task_id, str(exc))

    # ── Local file / filename helpers ────────────────────────────────────────

    def _find_existing_local_file(self, video_id: str) -> str | None:
        """Try to find an already-downloaded file by video ID in filename."""
        root = app_config.download_dir
        if not os.path.isdir(root):
            return None
        needle = video_id.lower()
        try:
            for dirpath, _, filenames in os.walk(root):
                for name in filenames:
                    lower_name = name.lower()
                    if lower_name.endswith("_temp") or lower_name.endswith(".aria2"):
                        continue
                    if needle in lower_name and lower_name.endswith(".mp4"):
                        full = os.path.join(dirpath, name)
                        if os.path.getsize(full) > 0:
                            return full
        except Exception:
            return None
        return None

    def _build_output_relative_path(
        self,
        *,
        title: str,
        video_id: str,
        author: str,
        published_at: str,
        quality: str,
        likes: int,
        views: int,
        comments: int,
        duration: int,
        slug: str,
        rating: str,
    ) -> str:
        raw_template = (
            app_config.filename_template or ""
        ).strip() or "{username}/{YYYY-MM-DD}_{title}_{id}.mp4"
        template = raw_template.replace("\\", "/")

        date_text = _extract_date_text(published_at) or datetime.now().strftime("%Y-%m-%d")
        year, month, day = date_text.split("-")
        username = (author or "unknown").strip() or "unknown"
        safe = lambda v: str(v).replace("/", "-").replace("\\", "-")

        mapping = {
            "{YYYY-MM-DD}": safe(date_text),
            "{YYYY}": safe(year),
            "{MM}": safe(month),
            "{DD}": safe(day),
            "{date}": safe(date_text),
            "{title}": safe(title),
            "{id}": safe(video_id),
            "{username}": safe(username),
            "{author}": safe(username),
            "{quality}": safe(quality or "unknown"),
            "{likes}": safe(str(likes)),
            "{views}": safe(str(views)),
            "{comments}": safe(str(comments)),
            "{duration}": safe(str(duration)),
            "{slug}": safe(slug),
            "{rating}": safe(rating),
        }
        for token, value in mapping.items():
            template = template.replace(token, str(value))

        parts = [p for p in template.split("/") if p.strip()]
        if not parts:
            parts = [f"{date_text}_{title}_{video_id}.mp4"]

        parts = [self._sanitize_path_segment(p) for p in parts]
        if not parts[-1].lower().endswith(".mp4"):
            parts[-1] += ".mp4"
        return os.path.join(*parts)

    @staticmethod
    def _sanitize_path_segment(name: str) -> str:
        cleaned = re.sub(r'[\\/:*?"<>|\t\r\n]', "-", name).strip(" .")
        if cleaned in ("", ".", ".."):
            return "_"
        return cleaned

    # ── Terminal state helpers ────────────────────────────────────────────────

    def _fail_task(self, task_id: str, reason: str):
        with self._lock:
            task = self._tasks.get(task_id)
            if task:
                task.status = TaskStatus.FAILED
                task.error_msg = reason
        signal_bus.task_status_changed.emit(task_id, TaskStatus.FAILED.value)
        signal_bus.task_error.emit(task_id, reason)
        # Free concurrency slot
        self._try_activate()

    def _finalize_temp_file(self, task_id: str, temp_path: str, final_path: str) -> bool:
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
    ):
        task.title = title
        task.author = author
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

    def _passes_filters(
        self,
        likes: int,
        views: int,
        published_at: str,
        tags: list[Any],
    ) -> tuple[bool, str]:
        if not app_config.filter_enabled:
            return True, ""

        if app_config.filter_min_likes_enabled and likes < app_config.filter_min_likes:
            return False, tr(
                f"likes {likes} < {app_config.filter_min_likes}",
                f"点赞 {likes} < {app_config.filter_min_likes}",
                f"いいね {likes} < {app_config.filter_min_likes}",
            )

        if app_config.filter_min_views_enabled and views < app_config.filter_min_views:
            return False, tr(
                f"views {views} < {app_config.filter_min_views}",
                f"播放 {views} < {app_config.filter_min_views}",
                f"再生数 {views} < {app_config.filter_min_views}",
            )

        if app_config.filter_date_enabled:
            date_text = _extract_date_text(published_at)
            if not date_text:
                return False, tr(
                    "invalid publish date",
                    "无有效发布日期",
                    "有効な公開日がありません",
                )
            start = app_config.filter_start_date or "1970-01-01"
            end = app_config.filter_end_date or datetime.now().strftime("%Y-%m-%d")
            if date_text < start or date_text > end:
                return False, tr(
                    f"date {date_text} is out of range {start} ~ {end}",
                    f"日期 {date_text} 不在 {start} ~ {end}",
                    f"日付 {date_text} が範囲外です {start} ~ {end}",
                )

        normalized_tags = _normalize_video_tags(tags)
        include_terms = _split_filter_tags(app_config.filter_include_tags)
        if app_config.filter_include_tags_enabled and include_terms:
            hit = [term for term in include_terms if term in normalized_tags]
            if not hit:
                return False, tr(
                    f"no include tags matched ({', '.join(include_terms)})",
                    f"未命中包含标签（{', '.join(include_terms)}）",
                    f"包含タグに一致しませんでした（{', '.join(include_terms)}）",
                )

        exclude_terms = _split_filter_tags(app_config.filter_exclude_tags)
        if app_config.filter_exclude_tags_enabled and exclude_terms:
            hit = [term for term in exclude_terms if term in normalized_tags]
            if hit:
                return False, tr(
                    f"matched exclude tags ({', '.join(hit)})",
                    f"命中排除标签（{', '.join(hit)}）",
                    f"除外タグに一致しました（{', '.join(hit)}）",
                )

        return True, ""

    def _skip_task(self, task_id: str, reason: str):
        with self._lock:
            task = self._tasks.get(task_id)
            if task:
                task.status = TaskStatus.SKIPPED
                task.error_msg = reason
        signal_bus.log_message.emit(
            tr(
                f"[Filtered] {task_id} -> {reason}",
                f"[筛选跳过] {task_id} → {reason}",
                f"[フィルター] {task_id} -> {reason}",
            )
        )
        signal_bus.task_status_changed.emit(task_id, TaskStatus.SKIPPED.value)
        self._try_activate()

    def _aria2_rpc_call(self, method: str, params: list) -> tuple[dict | None, str]:
        rpc_url = app_config.aria2_rpc_url.strip()
        if not rpc_url:
            return None, tr(
                "aria2 RPC URL is empty",
                "aria2 RPC URL 为空",
                "aria2 RPC URL が空です",
            )

        payload_params = list(params)
        token = app_config.aria2_rpc_token.strip()
        if token:
            payload_params.insert(0, f"token:{token}")

        payload = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": method,
            "params": payload_params,
        }

        try:
            resp = self.api.scraper.post(rpc_url, json=payload, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            return None, str(exc)

        if data.get("error"):
            return None, str(data.get("error"))
        return data, ""

    def _aria2_rpc_add_uri(self, uri: str, options: dict) -> tuple[str | None, str]:
        data, err = self._aria2_rpc_call("aria2.addUri", [[uri], options])
        if not data:
            return None, err
        gid = str(data.get("result", "") or "")
        if not gid:
            return None, tr(
                "aria2 did not return gid",
                "aria2 未返回 gid",
                "aria2 が gid を返しませんでした",
            )
        return gid, ""

    def _aria2_rpc_tell_status(self, gid: str) -> tuple[dict | None, str]:
        keys = ["status", "completedLength", "totalLength", "downloadSpeed", "errorMessage"]
        data, err = self._aria2_rpc_call("aria2.tellStatus", [gid, keys])
        if not data:
            return None, err
        result = data.get("result")
        if not isinstance(result, dict):
            return None, tr(
                f"Unexpected aria2 tellStatus result: {result!r}",
                f"aria2 tellStatus 返回异常: {result!r}",
                f"aria2 tellStatus の戻り値が不正です: {result!r}",
            )
        return result, ""

    def _aria2_rpc_remove_result(self, gid: str):
        self._aria2_rpc_call("aria2.removeDownloadResult", [gid])

    def _download_thumbnail(self, task: DownloadTask):
        if not task.file_path or not os.path.exists(task.file_path):
            return
        if os.path.getsize(task.file_path) <= 0:
            return
        if not task.file_id or not task.file_url:
            signal_bus.log_message.emit(
                tr(
                    f"  [Thumbnail] \"{task.title}\" missing file_id/file_url, skipped",
                    f"  [封面] 《{task.title}》 缺少 file_id/file_url，跳过",
                    f"  [サムネイル] 「{task.title}」file_id/file_url 欠落のためスキップ",
                )
            )
            return

        host = urlparse(task.file_url).netloc
        if not host:
            signal_bus.log_message.emit(
                tr(
                    f"  [Thumbnail] \"{task.title}\" invalid file_url, skipped",
                    f"  [封面] 《{task.title}》 无效 file_url，跳过",
                    f"  [サムネイル] 「{task.title}」無効な file_url のためスキップ",
                )
            )
            return

        thumbnail_path = os.path.splitext(task.file_path)[0] + ".jpg"
        temp_path = f"{thumbnail_path}_temp"
        if os.path.exists(thumbnail_path) and os.path.getsize(thumbnail_path) > 0:
            task.thumbnail_path = thumbnail_path
            return

        index = max(0, int(task.thumbnail_index))
        thumb_url = f"https://{host}/image/original/{task.file_id}/thumbnail-{index:02d}.jpg"
        try:
            resp = self.api.scraper.get(thumb_url, stream=True, timeout=60)
            if resp.status_code != 200:
                signal_bus.log_message.emit(
                    tr(
                        f"  [Thumbnail] \"{task.title}\" failed HTTP {resp.status_code}",
                        f"  [封面] 《{task.title}》 下载失败 HTTP {resp.status_code}",
                        f"  [サムネイル] 「{task.title}」HTTP {resp.status_code} 失敗",
                    )
                )
                return
            with open(temp_path, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk:
                        fh.write(chunk)

            if os.path.exists(temp_path) and os.path.getsize(temp_path) > 0:
                os.replace(temp_path, thumbnail_path)
                task.thumbnail_path = thumbnail_path
                signal_bus.log_message.emit(
                    tr(
                        f"  [Thumbnail] saved: {thumbnail_path}",
                        f"  [封面] 已保存: {thumbnail_path}",
                        f"  [サムネイル] 保存完了: {thumbnail_path}",
                    )
                )
                return

            if os.path.exists(temp_path):
                os.remove(temp_path)
        except Exception as exc:
            signal_bus.log_message.emit(
                tr(
                    f"  [Thumbnail] \"{task.title}\" error: {exc}",
                    f"  [封面] 《{task.title}》 下载异常: {exc}",
                    f"  [サムネイル] 「{task.title}」エラー: {exc}",
                )
            )

    def _write_nfo(self, task: DownloadTask):
        if not task.file_path or not os.path.exists(task.file_path):
            return
        if os.path.getsize(task.file_path) <= 0:
            return

        nfo_path = os.path.splitext(task.file_path)[0] + ".nfo"
        tags = _parse_tags(task.tags_json)
        nfo_text = _build_nfo_text(task, tags)

        temp_path = f"{nfo_path}_temp"
        try:
            with open(temp_path, "w", encoding="utf-8") as fh:
                fh.write(nfo_text)
            os.replace(temp_path, nfo_path)
            signal_bus.log_message.emit(
                tr(
                    f"  [NFO] saved: {nfo_path}",
                    f"  [NFO] 已保存: {nfo_path}",
                    f"  [NFO] 保存完了: {nfo_path}",
                )
            )
        except Exception as exc:
            signal_bus.log_message.emit(
                tr(
                    f"  [NFO] \"{task.title}\" write failed: {exc}",
                    f"  [NFO] 《{task.title}》 写入失败: {exc}",
                    f"  [NFO] 「{task.title}」書き込み失敗: {exc}",
                )
            )
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass

    def _complete_task(self, task_id: str):
        task = self._tasks.get(task_id)
        if not task:
            return
        with self._lock:
            task.status = TaskStatus.COMPLETED
        if app_config.download_thumbnail:
            self._download_thumbnail(task)
        if app_config.collect_nfo_info:
            self._write_nfo(task)
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
            signal_bus.log_message.emit(
                tr(
                    f"[Warning] Failed to write history DB (download file is safe): {exc}",
                    f"[警告] 写入历史库失败（不影响文件下载）: {exc}",
                    f"[警告] 履歴DB書き込み失敗（ダウンロードファイルには影響なし）: {exc}",
                )
            )
        signal_bus.task_status_changed.emit(task_id, TaskStatus.COMPLETED.value)
        # Free concurrency slot
        self._try_activate()


# ── Module-level singleton ────────────────────────────────────────────────────

download_manager = DownloadManager()


# ── Utility ──────────────────────────────────────────────────────────────────


def _fmt_speed(bps: float) -> str:
    if bps >= 1024**2:
        return f"{bps / 1024**2:.1f} MB/s"
    if bps >= 1024:
        return f"{bps / 1024:.1f} KB/s"
    return f"{bps:.0f} B/s"


def _fmt_bytes(n: int) -> str:
    if n >= 1024**3:
        return f"{n / 1024**3:.1f} GB"
    if n >= 1024**2:
        return f"{n / 1024**2:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n} B"


def _extract_date_text(published_at: str) -> str:
    if not published_at:
        return ""
    text = published_at.strip()
    if not text:
        return ""
    # Iwara often returns ISO 8601 with trailing Z.
    iso_text = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso_text)
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        m = re.match(r"^(\d{4}-\d{2}-\d{2})", text)
        return m.group(1) if m else ""


def _xml_text(value: str) -> str:
    return escape(value or "", {'"': "&quot;", "'": "&apos;"})


def _split_filter_tags(text: str) -> list[str]:
    if not text:
        return []
    parts = re.split(r"[\s,，;；|]+", text.strip())
    normalized: list[str] = []
    for part in parts:
        token = part.strip().lower().lstrip("#")
        if token and token not in normalized:
            normalized.append(token)
    return normalized


def _normalize_video_tags(tags: list[Any]) -> set[str]:
    normalized: set[str] = set()
    for item in tags:
        if isinstance(item, dict):
            for key in ("id", "type", "slug", "name", "title"):
                raw = str(item.get(key, "") or "").strip().lower().lstrip("#")
                if raw:
                    normalized.add(raw)
            continue
        text = str(item or "").strip().lower().lstrip("#")
        if text:
            normalized.add(text)
    return normalized


def _parse_tags(tags_json: str) -> list[str]:
    if not tags_json:
        return []
    try:
        data = json.loads(tags_json)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    tags: list[str] = []
    for item in data:
        if isinstance(item, dict):
            tag_text = str(item.get("id") or item.get("type") or "").strip()
            if tag_text:
                tags.append(tag_text)
            continue
        text = str(item).strip()
        if text:
            tags.append(text)
    return tags


def _build_nfo_text(task: DownloadTask, tags: list[str]) -> str:
    author_message = _extract_author_message(task.raw_json)
    tags_json_text = task.tags_json or json.dumps(tags, ensure_ascii=False)
    date_only = _extract_date_text(task.published_at)

    tag_lines = "\n".join(f"  <tag>{_xml_text(t)}</tag>" for t in tags)
    genre_lines = "\n".join(f"  <genre>{_xml_text(t)}</genre>" for t in tags)
    if tag_lines:
        tag_lines = f"\n{tag_lines}"
    if genre_lines:
        genre_lines = f"\n{genre_lines}"

    # Use movie-style XML for better media-library compatibility.
    return (
        "<?xml version=\"1.0\" encoding=\"utf-8\" standalone=\"yes\"?>\n"
        "<movie>\n"
        f"  <title>{_xml_text(task.title or task.video_id)}</title>\n"
        f"  <originaltitle>{_xml_text(task.title or task.video_id)}</originaltitle>\n"
        f"  <author>{_xml_text(task.author)}</author>\n"
        f"  <director>{_xml_text(task.author)}</director>\n"
        f"  <studio>{_xml_text(task.author)}</studio>\n"
        f"  <video_id>{_xml_text(task.video_id)}</video_id>\n"
        f"  <id>{_xml_text(task.video_id)}</id>\n"
        f"  <uniqueid type=\"iwara\" default=\"true\">{_xml_text(task.video_id)}</uniqueid>\n"
        f"  <source_url>{_xml_text(task.url)}</source_url>\n"
        f"  <slug>{_xml_text(task.slug)}</slug>\n"
        f"  <rating>{_xml_text(task.rating)}</rating>\n"
        f"  <duration>{task.duration}</duration>\n"
        f"  <published_at>{_xml_text(task.published_at)}</published_at>\n"
        f"  <premiered>{_xml_text(date_only)}</premiered>\n"
        f"  <releasedate>{_xml_text(date_only)}</releasedate>\n"
        f"  <likes>{task.likes}</likes>\n"
        f"  <views>{task.views}</views>\n"
        f"  <comments>{task.comments}</comments>\n"
        f"  <plot>{_xml_text(author_message)}</plot>\n"
        f"  <tags_json>{_xml_text(tags_json_text)}</tags_json>\n"
        f"  <author_message>{_xml_text(author_message)}</author_message>{genre_lines}{tag_lines}\n"
        "</movie>\n"
    )


def _extract_author_message(raw_json: str) -> str:
    if not raw_json:
        return ""
    try:
        data = json.loads(raw_json)
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""

    # Iwara API schemas are not perfectly stable; try common text fields.
    candidates = [
        data.get("body"),
        data.get("description"),
        data.get("message"),
    ]

    user = data.get("user")
    if isinstance(user, dict):
        candidates.extend(
            [
                user.get("body"),
                user.get("description"),
                user.get("bio"),
                user.get("about"),
            ]
        )
        profile = user.get("profile")
        if isinstance(profile, dict):
            candidates.extend(
                [
                    profile.get("body"),
                    profile.get("description"),
                    profile.get("bio"),
                    profile.get("about"),
                ]
            )

    for value in candidates:
        text = str(value or "").strip()
        if text:
            return text
    return ""
