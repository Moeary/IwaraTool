"""Download manager: state-machine scheduler + thread pool executor.

State machine flow
──────────────────
QUEUED_META → RESOLVING → QUEUED_DOWNLOAD → DOWNLOADING → COMPLETED
                                                        ↘ FAILED

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
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from ..config import app_config
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
        removed_ids: list[str] = []
        with self._lock:
            to_remove = [
                tid
                for tid, t in self._tasks.items()
                if t.status in (TaskStatus.COMPLETED, TaskStatus.FAILED)
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
                return False, "任务不存在"
            status = task.status
            file_path = task.file_path
            title = task.title or task.video_id

        if status != TaskStatus.COMPLETED:
            return False, "仅支持已完成任务"
        if not file_path or not os.path.exists(file_path):
            return False, "文件不存在"

        action = str(app_config.completed_task_click_action or "folder").lower()
        target = file_path if action == "player" else os.path.dirname(file_path)
        if not target:
            return False, "无可打开路径"

        try:
            if os.name == "nt":
                os.startfile(target)
            elif shutil.which("xdg-open"):
                subprocess.Popen(["xdg-open", target])
            elif shutil.which("open"):
                subprocess.Popen(["open", target])
            else:
                return False, "系统不支持自动打开"
        except Exception as exc:
            return False, str(exc)

        action_text = "播放器" if action == "player" else "文件夹"
        signal_bus.log_message.emit(f"[打开] 《{title}》 → {action_text}")
        return True, ""

    def set_login(self, logged_in: bool, token: str | None = None):
        if logged_in and token:
            self.api.token = token
        elif not logged_in:
            self.api.token = None

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
        signal_bus.log_message.emit(f"解析输入：{url}")

        parsed = self._parse_iwara_url(url)
        if parsed:
            kind, value = parsed
            if kind == "video":
                signal_bus.log_message.emit(f"[识别] 视频链接 → video_id={value}")
            elif kind == "user":
                signal_bus.log_message.emit(f"[识别] 用户链接 → username={value}")
            elif kind == "playlist":
                signal_bus.log_message.emit(
                    f"[识别] 播放列表链接 → playlist_id={value}"
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

        # Treat as raw video ID
        signal_bus.log_message.emit(f"[识别] 按视频ID处理 → {url}")
        self._enqueue_video_id(url, url)

    def _parse_iwara_url(self, raw_url: str) -> tuple[str, str] | None:
        """Parse iwara URLs into ('video'|'user'|'playlist', value)."""
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
                    f"[跳过] {video_id} 已存在本地文件: {existing_path}"
                )
                return

        with self._lock:
            # Dedup: skip if already in current queue
            for t in self._tasks.values():
                if t.video_id == video_id:
                    signal_bus.log_message.emit(f"[跳过] {video_id} 已在队列中")
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
            f"[入队] {video_id}（当前排队: {queued_count}，活动: {active_count}/{limit}）"
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
        signal_bus.log_message.emit(f"正在获取用户 [{username}] 的视频列表…")
        user_id, err = self.api.get_user_id(username)
        if not user_id:
            signal_bus.log_message.emit(f"[错误] 无法获取用户 ID: {err}")
            return
        videos = self.api.get_user_videos(user_id)
        signal_bus.log_message.emit(f"用户 [{username}] 共找到 {len(videos)} 个视频")
        for video in videos:
            vid = video.get("id", "")
            if vid:
                self._enqueue_video_id(vid, f"https://www.iwara.tv/video/{vid}")

    def _enqueue_playlist(self, playlist_id: str):
        signal_bus.log_message.emit(f"正在获取播放列表 [{playlist_id}] 的视频…")
        videos = self.api.get_playlist_videos(playlist_id)
        signal_bus.log_message.emit(f"播放列表共找到 {len(videos)} 个视频")
        for video in videos:
            vid = video.get("id", "")
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

        signal_bus.log_message.emit(f"[解析] 开始获取视频信息: {task.video_id}")

        video_info, err = self.api.get_video_info(task.video_id)
        if not video_info:
            self._fail_task(task_id, f"获取视频信息失败: {err}")
            signal_bus.log_message.emit(f"[失败] {task.video_id} → {err}")
            return

        # Re-try with login if private
        if (
            not video_info.get("fileUrl")
            and video_info.get("message") == "errors.privateVideo"
        ):
            if not self.api.token:
                self._fail_task(task_id, "私有视频，请先登录后重试")
                signal_bus.log_message.emit(
                    f"[跳过] {task.video_id} 为私有视频，请先登录"
                )
                return
            self._fail_task(task_id, "私有视频（已登录但无权限）")
            signal_bus.log_message.emit(f"[跳过] {task.video_id} 私有视频，无权限")
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
        tags_json = json.dumps(video_info.get("tags", []), ensure_ascii=False)
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
        )
        if not passed_filter:
            self._skip_task(task_id, f"筛选不通过: {filter_reason}")
            return

        signal_bus.log_message.emit(f"[解析] 《{title}》 by {author}")

        # Pass quality preference and a logging callback
        pref_quality = app_config.preferred_quality
        signal_bus.log_message.emit(
            f"[解析] 首选画质: {pref_quality}，开始获取文件列表…"
        )

        def _log(msg: str):
            signal_bus.log_message.emit(msg)

        dl_url, quality, err2 = self.api.get_download_info(
            video_info,
            preferred_quality=pref_quality,
            log_cb=_log,
        )
        if not dl_url:
            self._fail_task(task_id, f"解析下载链接失败: {err2}")
            signal_bus.log_message.emit(f"[失败] 《{title}》 解析失败 → {err2}")
            return

        signal_bus.log_message.emit(f"[解析完成] 《{title}》 画质={quality}")

        filename = self._build_filename(
            title=title,
            video_id=task.video_id,
            published_at=published_at,
        )
        save_dir = os.path.join(app_config.download_dir, author or "unknown")
        file_path = os.path.join(save_dir, filename)

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
            signal_bus.log_message.emit(f"[跳过] 《{title}》 本地已存在，标记完成")
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
        save_dir = os.path.join(app_config.download_dir, task.author or "unknown")
        os.makedirs(save_dir, exist_ok=True)
        final_path = os.path.join(save_dir, task.filename)
        temp_path = f"{final_path}_temp"

        with self._lock:
            task.file_path = final_path

        signal_bus.log_message.emit(f"[下载] 《{task.title}》 [画质:{task.quality}]")
        signal_bus.log_message.emit(f"  保存至: {final_path}")
        signal_bus.log_message.emit(f"  临时文件: {temp_path}")

        if app_config.aria2_rpc_enabled:
            self._download_task_aria2(
                task_id,
                final_path=final_path,
                temp_path=temp_path,
            )
            return

        signal_bus.log_message.emit("  aria2 未启用，使用内置下载器")
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
            signal_bus.log_message.emit("  aria2 RPC 地址为空，回退到内置下载器")
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

        signal_bus.log_message.emit(f"  使用 aria2 RPC 下载: {rpc_url}")
        gid, add_err = self._aria2_rpc_add_uri(task.download_url, options)
        if not gid:
            signal_bus.log_message.emit(f"  aria2 RPC 提交失败，回退到内置下载器: {add_err}")
            self._download_task_native(task_id, final_path=final_path, temp_path=temp_path)
            return

        last_emit = 0.0
        while True:
            status_info, err = self._aria2_rpc_tell_status(gid)
            if not status_info:
                self._fail_task(task_id, f"aria2 RPC 查询失败: {err}")
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
                signal_bus.log_message.emit(f"[完成] 《{task.title}》 总大小 {_fmt_bytes(downloaded)}")
                self._complete_task(task_id)
                self._aria2_rpc_remove_result(gid)
                return

            if status in ("error", "removed"):
                err_msg = str(status_info.get("errorMessage", "aria2 未知错误") or "aria2 未知错误")
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
                        f"  断点续传: 已有 {_fmt_bytes(existing_size)}"
                    )

            resp = self.api.scraper.get(
                task.download_url, headers=headers, stream=True, timeout=60
            )
            signal_bus.log_message.emit(
                f"  HTTP {resp.status_code}  Content-Length: {resp.headers.get('Content-Length', '?')}"
            )

            if resp.status_code == 416:
                signal_bus.log_message.emit("  文件已完整，标记完成")
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

            signal_bus.log_message.emit(f"  文件总大小: {_fmt_bytes(total)}")

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
                f"[完成] 《{task.title}》 总大小 {_fmt_bytes(downloaded)}"
            )
            self._complete_task(task_id)

        except Exception as exc:
            signal_bus.log_message.emit(f"[下载异常] 《{task.title}》 → {exc}")
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

    def _build_filename(self, title: str, video_id: str, published_at: str = "") -> str:
        template = (
            app_config.filename_template or ""
        ).strip() or "{YYYY-MM-DD}+{title}+{id}.mp4"
        date_text = _extract_date_text(published_at) or datetime.now().strftime("%Y-%m-%d")
        mapping = {
            "{YYYY-MM-DD}": date_text,
            "{title}": title,
            "{id}": video_id,
        }

        for token, value in mapping.items():
            template = template.replace(token, value)

        template = self._sanitize_filename(template)
        if not template.lower().endswith(".mp4"):
            template += ".mp4"
        return template

    @staticmethod
    def _sanitize_filename(name: str) -> str:
        cleaned = re.sub(r'[\\/:*?"<>|\t\r\n]', "-", name).strip(" .")
        return cleaned or "video.mp4"

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
            self._fail_task(task_id, "临时文件不存在，无法完成重命名")
            return False
        size = os.path.getsize(temp_path)
        if size <= 0:
            self._fail_task(task_id, "临时文件为空，下载不完整")
            return False
        try:
            os.replace(temp_path, final_path)
            sidecar = f"{temp_path}.aria2"
            if os.path.exists(sidecar):
                os.remove(sidecar)
            return True
        except Exception as exc:
            self._fail_task(task_id, f"重命名临时文件失败: {exc}")
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

    def _passes_filters(self, likes: int, views: int, published_at: str) -> tuple[bool, str]:
        if not app_config.filter_enabled:
            return True, ""

        if app_config.filter_min_likes_enabled and likes < app_config.filter_min_likes:
            return False, f"点赞 {likes} < {app_config.filter_min_likes}"

        if app_config.filter_min_views_enabled and views < app_config.filter_min_views:
            return False, f"播放 {views} < {app_config.filter_min_views}"

        if app_config.filter_date_enabled:
            date_text = _extract_date_text(published_at)
            if not date_text:
                return False, "无有效发布日期"
            start = app_config.filter_start_date or "1970-01-01"
            end = app_config.filter_end_date or datetime.now().strftime("%Y-%m-%d")
            if date_text < start or date_text > end:
                return False, f"日期 {date_text} 不在 {start} ~ {end}"

        return True, ""

    def _skip_task(self, task_id: str, reason: str):
        with self._lock:
            task = self._tasks.get(task_id)
            if task:
                task.status = TaskStatus.COMPLETED
                task.error_msg = reason
        signal_bus.log_message.emit(f"[筛选跳过] {task_id} → {reason}")
        signal_bus.task_status_changed.emit(task_id, TaskStatus.COMPLETED.value)
        self._try_activate()

    def _aria2_rpc_call(self, method: str, params: list) -> tuple[dict | None, str]:
        rpc_url = app_config.aria2_rpc_url.strip()
        if not rpc_url:
            return None, "aria2 RPC URL 为空"

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
            return None, "aria2 未返回 gid"
        return gid, ""

    def _aria2_rpc_tell_status(self, gid: str) -> tuple[dict | None, str]:
        keys = ["status", "completedLength", "totalLength", "downloadSpeed", "errorMessage"]
        data, err = self._aria2_rpc_call("aria2.tellStatus", [gid, keys])
        if not data:
            return None, err
        result = data.get("result")
        if not isinstance(result, dict):
            return None, f"aria2 tellStatus 返回异常: {result!r}"
        return result, ""

    def _aria2_rpc_remove_result(self, gid: str):
        self._aria2_rpc_call("aria2.removeDownloadResult", [gid])

    def _download_thumbnail(self, task: DownloadTask):
        if not task.file_path or not os.path.exists(task.file_path):
            return
        if os.path.getsize(task.file_path) <= 0:
            return
        if not task.file_id or not task.file_url:
            signal_bus.log_message.emit(f"  [封面] 《{task.title}》 缺少 file_id/file_url，跳过")
            return

        host = urlparse(task.file_url).netloc
        if not host:
            signal_bus.log_message.emit(f"  [封面] 《{task.title}》 无效 file_url，跳过")
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
                    f"  [封面] 《{task.title}》 下载失败 HTTP {resp.status_code}"
                )
                return
            with open(temp_path, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk:
                        fh.write(chunk)

            if os.path.exists(temp_path) and os.path.getsize(temp_path) > 0:
                os.replace(temp_path, thumbnail_path)
                task.thumbnail_path = thumbnail_path
                signal_bus.log_message.emit(f"  [封面] 已保存: {thumbnail_path}")
                return

            if os.path.exists(temp_path):
                os.remove(temp_path)
        except Exception as exc:
            signal_bus.log_message.emit(f"  [封面] 《{task.title}》 下载异常: {exc}")

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
            signal_bus.log_message.emit(f"  [NFO] 已保存: {nfo_path}")
        except Exception as exc:
            signal_bus.log_message.emit(f"  [NFO] 《{task.title}》 写入失败: {exc}")
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
            signal_bus.log_message.emit(f"[警告] 写入历史库失败（不影响文件下载）: {exc}")
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
