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
import re
import shutil
import subprocess
import threading
import time
import uuid
from collections import deque
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

    def remove_task(self, task_id: str):
        with self._lock:
            self._tasks.pop(task_id, None)

    def clear_completed(self):
        with self._lock:
            to_remove = [
                tid
                for tid, t in self._tasks.items()
                if t.status in (TaskStatus.COMPLETED, TaskStatus.FAILED)
            ]
            for tid in to_remove:
                del self._tasks[tid]

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
        # Dedup: skip if already in history
        if self.history.is_downloaded(video_id):
            signal_bus.log_message.emit(f"[跳过] {video_id} 已在下载记录中")
            return

        # Optional: skip if same ID appears in local download directory
        if app_config.skip_existing_files:
            existing_path = self._find_existing_local_file(video_id)
            if existing_path:
                self.history.add_downloaded(video_id)
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

        filename = self._build_filename(title=title, video_id=task.video_id)
        save_dir = os.path.join(app_config.download_dir, author or "unknown")
        file_path = os.path.join(save_dir, filename)

        if (
            app_config.skip_existing_files
            and os.path.exists(file_path)
            and os.path.getsize(file_path) > 0
        ):
            with self._lock:
                task.title = title
                task.author = author
                task.download_url = dl_url
                task.quality = quality or ""
                task.filename = filename
                task.file_path = file_path
            signal_bus.log_message.emit(f"[跳过] 《{title}》 本地已存在，标记完成")
            self._complete_task(task_id)
            return

        with self._lock:
            task.title = title
            task.author = author
            task.download_url = dl_url
            task.quality = quality or ""
            task.filename = filename
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
        file_path = os.path.join(save_dir, task.filename)

        with self._lock:
            task.file_path = file_path

        signal_bus.log_message.emit(f"[下载] 《{task.title}》 [画质:{task.quality}]")
        signal_bus.log_message.emit(f"  保存至: {file_path}")

        aria2_path = shutil.which("aria2c")
        if aria2_path:
            self._download_task_aria2(
                task_id, file_path=file_path, aria2_path=aria2_path
            )
            return

        signal_bus.log_message.emit("  未找到 aria2c，回退到内置下载器")
        self._download_task_native(task_id, file_path=file_path)

    def _download_task_aria2(self, task_id: str, file_path: str, aria2_path: str):
        task = self._tasks.get(task_id)
        if not task:
            return

        save_dir = os.path.dirname(file_path)
        filename = os.path.basename(file_path)

        cmd = [
            aria2_path,
            "--continue=true",
            "--max-connection-per-server=16",
            "--split=16",
            "--min-split-size=1M",
            "--summary-interval=1",
            "--download-result=hide",
            "--console-log-level=warn",
            "--auto-file-renaming=false",
            "--allow-overwrite=false",
            "--file-allocation=none",
            "--timeout=60",
            "--max-tries=5",
            "--retry-wait=2",
            "--dir",
            save_dir,
            "--out",
            filename,
            task.download_url,
        ]
        if self.api.token:
            cmd.insert(-1, f"--header=Authorization: Bearer {self.api.token}")
        if app_config.proxy_enabled and app_config.proxy_url:
            cmd.insert(-1, f"--all-proxy={app_config.proxy_url}")

        signal_bus.log_message.emit("  使用 aria2c 多连接下载")

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except Exception as exc:
            signal_bus.log_message.emit(f"  aria2c 启动失败，回退到内置下载器: {exc}")
            self._download_task_native(task_id, file_path=file_path)
            return

        progress_re = re.compile(
            r"(?P<done>\d+(?:\.\d+)?[KMGT]?i?B)/(?P<total>\d+(?:\.\d+)?[KMGT]?i?B)\((?P<pct>\d+)%\)"
        )
        speed_re = re.compile(r"DL:(?P<speed>\d+(?:\.\d+)?[KMGT]?i?B)")
        tail = deque(maxlen=8)
        last_emit = 0.0

        if proc.stdout:
            for line in proc.stdout:
                text = line.strip()
                if not text:
                    continue
                tail.append(text)

                done_bytes = None
                total_bytes = None
                speed_str = ""

                m_prog = progress_re.search(text)
                if m_prog:
                    done_bytes = _parse_aria2_size(m_prog.group("done"))
                    total_bytes = _parse_aria2_size(m_prog.group("total"))

                m_speed = speed_re.search(text)
                if m_speed:
                    speed_bps = _parse_aria2_size(m_speed.group("speed"))
                    if speed_bps is not None:
                        speed_str = _fmt_speed(float(speed_bps))

                now = time.monotonic()
                if now - last_emit >= 0.5:
                    if done_bytes is None:
                        done_bytes = (
                            os.path.getsize(file_path)
                            if os.path.exists(file_path)
                            else 0
                        )
                    if total_bytes is None:
                        total_bytes = done_bytes
                    if total_bytes < done_bytes:
                        total_bytes = done_bytes

                    with self._lock:
                        task.downloaded_bytes = done_bytes
                        task.total_bytes = total_bytes
                        task.speed_str = speed_str

                    signal_bus.task_progress_updated.emit(
                        task_id, done_bytes, total_bytes, speed_str
                    )
                    last_emit = now

        rc = proc.wait()
        if rc != 0:
            reason = f"aria2c 退出码 {rc}"
            if tail:
                reason = f"{reason}: {tail[-1]}"
            self._fail_task(task_id, reason)
            return

        downloaded = os.path.getsize(file_path) if os.path.exists(file_path) else 0
        total = downloaded
        with self._lock:
            task.downloaded_bytes = downloaded
            task.total_bytes = total
            task.speed_str = ""
        signal_bus.task_progress_updated.emit(task_id, downloaded, total, "")
        signal_bus.log_message.emit(
            f"[完成] 《{task.title}》 总大小 {_fmt_bytes(downloaded)}"
        )
        self._complete_task(task_id)

    def _download_task_native(self, task_id: str, file_path: str):
        task = self._tasks.get(task_id)
        if not task:
            return

        try:
            headers: dict[str, str] = {}
            if self.api.token:
                headers["Authorization"] = f"Bearer {self.api.token}"

            # Resume support
            existing_size = 0
            if os.path.exists(file_path):
                existing_size = os.path.getsize(file_path)
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

            with open(file_path, mode) as fh:
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
                    if needle in lower_name and lower_name.endswith(".mp4"):
                        full = os.path.join(dirpath, name)
                        if os.path.getsize(full) > 0:
                            return full
        except Exception:
            return None
        return None

    def _build_filename(self, title: str, video_id: str) -> str:
        template = (
            app_config.filename_template or ""
        ).strip() or "{YYYY-MM-DD}+{title}+{id}.mp4"
        date_text = datetime.now().strftime("%Y-%m-%d")
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

    def _complete_task(self, task_id: str):
        task = self._tasks.get(task_id)
        if not task:
            return
        with self._lock:
            task.status = TaskStatus.COMPLETED
        self.history.add_downloaded(task.video_id)
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


def _parse_aria2_size(text: str) -> int | None:
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)([KMGT]?i?B)\s*", text)
    if not m:
        return None
    value = float(m.group(1))
    unit = m.group(2).upper()
    scale = {
        "B": 1,
        "KIB": 1024,
        "MIB": 1024**2,
        "GIB": 1024**3,
        "TIB": 1024**4,
        "KB": 1024,
        "MB": 1024**2,
        "GB": 1024**3,
        "TB": 1024**4,
    }.get(unit)
    if scale is None:
        return None
    return int(value * scale)
