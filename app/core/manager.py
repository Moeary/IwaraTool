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

import gc
import hashlib
import os
import json
import re
import shutil
import subprocess
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable
from urllib.parse import parse_qs, quote, urlparse

from ..config import app_config
from ..i18n import tr
from ..signal_bus import signal_bus
from .api import IwaraAPI
from .history import DownloadHistory
from .models import DownloadTask, TaskStatus
from .nfo import build_nfo_text, parse_tags as parse_nfo_tags
from .subscriptions import SubscriptionStore

if TYPE_CHECKING:
    pass


_ACTIVE_STATUSES = frozenset(
    [
        TaskStatus.RESOLVING,
        TaskStatus.QUEUED_DOWNLOAD,
        TaskStatus.DOWNLOADING,
        TaskStatus.CANCELLING,
    ]
)

_TERMINAL_STATUSES = frozenset(
    [
        TaskStatus.COMPLETED,
        TaskStatus.SKIPPED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    ]
)

_PRUNABLE_TERMINAL_STATUSES = frozenset(
    [
        TaskStatus.COMPLETED,
        TaskStatus.SKIPPED,
        TaskStatus.FAILED,
    ]
)

_STALL_WATCH_STATUSES = frozenset(
    [
        TaskStatus.RESOLVING,
        TaskStatus.QUEUED_DOWNLOAD,
        TaskStatus.DOWNLOADING,
    ]
)

_LIVE_TERMINAL_KEEP_LIMIT = 100
_EXISTING_FILE_INDEX_TTL_SECONDS = 60
_MAX_STORED_TEXT_CHARS = 20000
_STALL_WATCHDOG_INTERVAL_SECONDS = 1.0
_SUBSCRIPTION_UNAVAILABLE_STATE = "unavailable"
_CANCEL_ORIGIN_AUTO_STALL = "auto_stall"
_CANCEL_ORIGIN_MANUAL = "manual"
_WINDOWS_SAFE_PATH_LIMIT = 240
_WINDOWS_MIN_PATH_SEGMENT_LENGTH = 32
_WINDOWS_RESERVED_FILENAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{number}" for number in range(1, 10)),
        *(f"LPT{number}" for number in range(1, 10)),
    }
)


class DownloadManager:
    """Central manager for all download tasks.

    Thread-safe: all internal state mutations are protected by self._lock.
    Qt signals are emitted *outside* the lock to avoid deadlocks.
    """

    def __init__(self):
        self.api = IwaraAPI()
        self.history = DownloadHistory()
        self.subscriptions = SubscriptionStore()

        # task_id → DownloadTask
        self._tasks: dict[str, DownloadTask] = {}
        self._task_id_by_video_id: dict[str, str] = {}
        self._queued_meta_ids: deque[str] = deque()
        self._active_task_ids: set[str] = set()
        self._terminal_task_ids: deque[str] = deque()
        self._terminal_task_id_set: set[str] = set()
        self._lock = threading.Lock()
        self._api_lock = threading.RLock()
        self._existing_file_index_lock = threading.Lock()
        self._existing_file_index: dict[str, str] = {}
        self._existing_file_index_root = ""
        self._existing_file_index_built_at = 0.0
        self._terminal_keep_limit = _LIVE_TERMINAL_KEEP_LIMIT
        self._terminal_events_since_gc = 0
        self._last_gc_at = 0.0
        self._task_last_activity: dict[str, float] = {}

        # Keep parse, resolve, and download work isolated so a large batch cannot
        # starve metadata resolution or leave the UI looking stuck.
        self._parse_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="iwara-parse")
        self._resolve_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="iwara-resolve")
        self._download_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="iwara-download")
        self._watchdog_stop = threading.Event()
        self._watchdog_thread = threading.Thread(
            target=self._stall_watchdog_loop,
            name="iwara-stall-watchdog",
            daemon=True,
        )
        self._watchdog_thread.start()

    # ── Public API ────────────────────────────────────────────────────────────

    def get_tasks(self) -> list[DownloadTask]:
        with self._lock:
            return list(self._tasks.values())

    def get_task(self, task_id: str) -> DownloadTask | None:
        with self._lock:
            return self._tasks.get(task_id)

    def add_url(self, url: str):
        """Parse URL and enqueue tasks (runs in background thread)."""
        self._parse_executor.submit(self._parse_and_enqueue, url)

    def add_url_mark_downloaded(self, url: str):
        """Parse URL and mark resolved videos as already downloaded in history."""
        self._parse_executor.submit(self._parse_and_mark_downloaded, url)

    def enqueue_video_ids(self, video_ids: list[str], *, source_label: str = "") -> int:
        """Queue a list of video ids and return how many were accepted for parsing."""
        items = [
            (str(raw_id or "").strip(), f"https://www.iwara.tv/video/{str(raw_id or '').strip()}")
            for raw_id in video_ids
            if str(raw_id or "").strip()
        ]
        summary = self._enqueue_video_ids_bulk(items, source_label=source_label)
        return int(summary.get("queued", 0) or 0)

    def retry_task(self, task_id: str):
        """Re-queue a failed task."""
        temp_file_path = ""
        temp_title = ""
        with self._lock:
            task = self._tasks.get(task_id)
            if task and task.status == TaskStatus.FAILED:
                temp_file_path = task.file_path
                temp_title = task.title or task.video_id
        if not temp_title:
            return

        _, cleanup_failed = self._log_retry_temp_cleanup(temp_title, temp_file_path)
        if cleanup_failed:
            signal_bus.task_error.emit(
                task_id,
                tr(
                    "Temp cache cleanup failed; retry was not started",
                    "临时缓存清理失败，未开始重试",
                    "一時キャッシュ削除に失敗したため再試行しません",
                ),
            )
            return

        requeued = False
        with self._lock:
            task = self._tasks.get(task_id)
            if task and task.status == TaskStatus.FAILED:
                task.status = TaskStatus.QUEUED_META
                task.cancel_requested = False
                task.delete_temp_on_cancel = False
                task.remove_after_cancel = False
                task.cancel_origin = ""
                task.aria2_gid = ""
                task.error_msg = ""
                task.downloaded_bytes = 0
                task.total_bytes = 0
                task.download_url = ""
                self._unmark_terminal_locked(task_id)
                self._queued_meta_ids.append(task_id)
                requeued = True
        if not requeued:
            return
        signal_bus.task_status_changed.emit(task_id, TaskStatus.QUEUED_META.value)
        self._try_activate()

    def restore_cancelled_task(self, task_id: str) -> bool:
        """Put a cancelled task back into the queue without deleting temp data."""
        title = ""
        requeued = False
        with self._lock:
            task = self._tasks.get(task_id)
            if not task or task.status != TaskStatus.CANCELLED:
                return False
            title = task.title or task.video_id
            self._restore_cancelled_task_locked(task)
            requeued = True

        if not requeued:
            return False
        signal_bus.log_message.emit(
            tr(
                f"[Restore] \"{title}\" re-queued",
                f"[复原] 《{title}》已重新加入队列",
                f"[復元] 「{title}」をキューに戻しました",
            )
        )
        signal_bus.task_status_changed.emit(task_id, TaskStatus.QUEUED_META.value)
        self._try_activate()
        return True

    def restore_all_cancelled(self) -> int:
        """Put all cancelled tasks back into the queue."""
        return self._restore_cancelled_tasks(origin_filter="")

    def _restore_auto_stalled_cancelled_if_idle(self) -> int:
        if not app_config.auto_restore_stalled_cancelled:
            return 0
        return self._restore_cancelled_tasks(origin_filter=_CANCEL_ORIGIN_AUTO_STALL, only_when_idle=True)

    def _restore_cancelled_tasks(self, *, origin_filter: str = "", only_when_idle: bool = False) -> int:
        """Put cancelled tasks back into the queue, optionally filtered by origin."""
        restored_ids: list[str] = []
        with self._lock:
            if only_when_idle and any(
                task.status not in _TERMINAL_STATUSES
                for task in self._tasks.values()
            ):
                return 0
            for task in list(self._tasks.values()):
                if task.status != TaskStatus.CANCELLED:
                    continue
                if origin_filter and task.cancel_origin != origin_filter:
                    continue
                self._restore_cancelled_task_locked(task)
                restored_ids.append(task.task_id)

        for task_id in restored_ids:
            signal_bus.task_status_changed.emit(task_id, TaskStatus.QUEUED_META.value)
        if restored_ids:
            signal_bus.log_message.emit(
                tr(
                    f"[Restore] re-queued {len(restored_ids)} cancelled tasks",
                    f"[复原] 已重新加入队列 {len(restored_ids)} 个中断任务",
                    f"[復元] {len(restored_ids)} 件の中断タスクをキューへ戻しました",
                )
            )
            self._try_activate()
        return len(restored_ids)

    def retry_all_failed(self, exclude_downloaded: bool = True) -> tuple[int, int]:
        """Retry all failed tasks.

        Args:
            exclude_downloaded: if True, failed tasks that already have local
                completed files are marked completed instead of retried.

        Returns:
            (retried_count, skipped_as_completed_count)
        """
        to_retry: list[tuple[str, str, str]] = []
        to_complete: list[str] = []

        with self._lock:
            failed_tasks = [
                t for t in self._tasks.values() if t.status == TaskStatus.FAILED
            ]

        for task in failed_tasks:
            if exclude_downloaded and self._find_existing_local_file(task.video_id):
                to_complete.append(task.task_id)
                continue

            to_retry.append(
                (task.task_id, task.file_path, task.title or task.video_id)
            )

        retried_count = 0
        for tid in to_complete:
            self._complete_task(tid)
        for tid, file_path, title in to_retry:
            _, cleanup_failed = self._log_retry_temp_cleanup(title, file_path)
            if cleanup_failed:
                signal_bus.task_error.emit(
                    tid,
                    tr(
                        "Temp cache cleanup failed; retry was not started",
                        "临时缓存清理失败，未开始重试",
                        "一時キャッシュ削除に失敗したため再試行しません",
                    ),
                )
                continue
            with self._lock:
                task = self._tasks.get(tid)
                if not task or task.status != TaskStatus.FAILED:
                    continue
                task.status = TaskStatus.QUEUED_META
                task.cancel_requested = False
                task.delete_temp_on_cancel = False
                task.remove_after_cancel = False
                task.cancel_origin = ""
                task.aria2_gid = ""
                task.error_msg = ""
                task.downloaded_bytes = 0
                task.total_bytes = 0
                task.download_url = ""
                self._unmark_terminal_locked(tid)
                self._queued_meta_ids.append(tid)
            retried_count += 1
            signal_bus.task_status_changed.emit(tid, TaskStatus.QUEUED_META.value)

        self._try_activate()
        return retried_count, len(to_complete)

    def cancel_task(
        self,
        task_id: str,
        *,
        delete_temp: bool = False,
        remove_after_cancel: bool = False,
    ) -> bool:
        """Request cancellation for a queued or active task."""
        finalize_now = False
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return False
            if task.status in _TERMINAL_STATUSES:
                return False
            task.cancel_requested = True
            task.cancel_origin = _CANCEL_ORIGIN_MANUAL
            task.delete_temp_on_cancel = task.delete_temp_on_cancel or delete_temp
            task.remove_after_cancel = task.remove_after_cancel or remove_after_cancel
            if task.status in (TaskStatus.QUEUED_META, TaskStatus.QUEUED_DOWNLOAD):
                finalize_now = True
            else:
                task.status = TaskStatus.CANCELLING
            self._task_last_activity.pop(task_id, None)

        if finalize_now:
            self._cancel_task_terminal(
                task_id,
                tr("Cancelled by user", "用户已中断", "ユーザーが中断しました"),
            )
        else:
            signal_bus.task_status_changed.emit(task_id, TaskStatus.CANCELLING.value)
        return True

    def cancel_all_active(self) -> int:
        with self._lock:
            ids = [
                tid
                for tid, task in self._tasks.items()
                if task.status not in _TERMINAL_STATUSES
            ]
        for task_id in ids:
            self.cancel_task(task_id)
        return len(ids)

    def remove_task(self, task_id: str):
        should_cancel = False
        with self._lock:
            task = self._tasks.get(task_id)
            if task and task.status not in _TERMINAL_STATUSES:
                should_cancel = True
                removed = None
            else:
                removed = self._forget_task_locked(task_id)
        if should_cancel:
            self.cancel_task(task_id, remove_after_cancel=True)
            return
        if removed:
            signal_bus.task_removed.emit(task_id)

    def clear_completed(self):
        """Remove finished tasks from UI board, keeping retry/restore candidates."""
        removed_ids: list[str] = []
        with self._lock:
            to_remove = [
                tid
                for tid, t in self._tasks.items()
                if t.status in (TaskStatus.COMPLETED, TaskStatus.SKIPPED)
            ]
            for tid in to_remove:
                if self._forget_task_locked(tid):
                    removed_ids.append(tid)
        for tid in removed_ids:
            signal_bus.task_removed.emit(tid)

    def get_history_records(self) -> list[dict[str, Any]]:
        return self.history.list_records()

    def sync_history_with_download_folder(self) -> dict[str, int]:
        return self.history.sync_with_download_folder(app_config.download_dir)

    def remove_history_record(self, video_id: str):
        self.history.remove(video_id)

    # ── Subscriptions ────────────────────────────────────────────────────────

    def add_following_subscription(self) -> int:
        return self.subscriptions.add_source(
            "feed",
            "subscribed",
            tr("Following Feed", "账号订阅流", "購読フィード"),
        )

    def import_followed_author_subscriptions(self) -> dict[str, Any]:
        """Import followed authors from the logged-in account.

        Prefer the explicit following-user endpoint. If it cannot be used
        (usually because the saved credential is an email rather than username),
        fall back to deriving authors from the subscribed video feed.
        """
        if not self.api.token:
            return {
                "imported": 0,
                "method": "",
                "error": tr("Please login first", "请先登录账号", "先にログインしてください"),
            }

        username = (app_config.username or "").strip()
        if username and "@" not in username:
            user_id, err = self._api_call("get_user_id", username)
            if user_id:
                users, follow_err = self._api_call("get_user_following", user_id)
                if not follow_err:
                    imported = self._add_author_sources_from_users(users)
                    return {
                        "imported": imported,
                        "method": "following",
                        "error": "",
                    }
                err = follow_err
            signal_bus.log_message.emit(
                tr(
                    f"[Subscriptions] following endpoint failed, fallback to feed authors: {err}",
                    f"[订阅] 关注作者接口失败，回退为从订阅视频流提取作者: {err}",
                    f"[購読] フォロー取得失敗、購読フィードから作者を抽出: {err}",
                )
            )

        cap = max(500, self._subscription_fetch_limit() or 500)
        videos, err = self._api_call("get_subscribed_videos", max_results=cap)
        if err:
            return {"imported": 0, "method": "feed", "error": err}
        imported = self._add_author_sources_from_videos(videos)
        return {
            "imported": imported,
            "method": "feed",
            "error": "" if imported else tr(
                "No authors found in subscribed feed",
                "订阅视频流里没有提取到作者",
                "購読フィードから作者を抽出できませんでした",
            ),
        }

    def add_author_subscription(self, username: str) -> int:
        username = username.strip().strip("/")
        return self.subscriptions.add_source("author", username, username)

    def add_playlist_subscription(self, playlist_id: str) -> int:
        playlist_id = playlist_id.strip().strip("/")
        return self.subscriptions.add_source(
            "playlist",
            playlist_id,
            tr(f"Playlist {playlist_id}", f"播放列表 {playlist_id}", f"プレイリスト {playlist_id}"),
        )

    def detect_subscription_source(self, raw: str) -> tuple[str, str] | None:
        parsed = self._parse_iwara_url(raw)
        if not parsed:
            return None
        kind, value = parsed
        if kind == "user":
            return "author", str(value)
        if kind == "playlist":
            return "playlist", str(value)
        return None

    def add_detected_subscription_source(self, kind: str, key: str) -> int:
        if kind == "playlist":
            return self.add_playlist_subscription(key)
        if kind == "author":
            return self.add_author_subscription(key)
        return 0

    def get_subscription_sources(self) -> list[dict[str, Any]]:
        sources = self.subscriptions.list_sources()
        if not sources:
            return sources
        counts_by_source: dict[int, dict[str, int]] = {}
        items = self.subscriptions.list_items(None)
        video_ids = [str(item.get("video_id", "") or "") for item in items]
        history_records = self.history.get_records(video_ids)
        for item in items:
            source_id = int(item.get("source_id", 0) or 0)
            video_id = str(item.get("video_id", "") or "")
            if not source_id or not video_id:
                continue
            counts = counts_by_source.setdefault(
                source_id,
                {"downloaded_count": 0, "undownloaded_count": 0, "unavailable_count": 0},
            )
            if video_id in history_records:
                counts["downloaded_count"] += 1
            elif str(item.get("download_state", "") or "") == _SUBSCRIPTION_UNAVAILABLE_STATE:
                counts["unavailable_count"] += 1
            else:
                counts["undownloaded_count"] += 1
        for source in sources:
            source_id = int(source.get("id", 0) or 0)
            counts = counts_by_source.get(source_id, {})
            source["downloaded_count"] = int(counts.get("downloaded_count", 0) or 0)
            source["undownloaded_count"] = int(counts.get("undownloaded_count", 0) or 0)
            source["unavailable_count"] = int(counts.get("unavailable_count", 0) or 0)
        return sources

    def get_subscription_storage_info(self) -> dict[str, str]:
        return {
            "db_path": self.subscriptions.db_path,
            "backup_path": self.subscriptions.backup_path,
        }

    def get_subscription_items(self, source_id: int | None = None) -> list[dict[str, Any]]:
        items = self.subscriptions.list_items(source_id)
        video_ids = [str(item.get("video_id", "") or "") for item in items]
        history_records = self.history.get_records(video_ids)
        with self._lock:
            task_info_by_video_id = {
                task.video_id.lower(): (task.status.value, task.error_msg)
                for task in self._tasks.values()
                if task.video_id
            }
        for item in items:
            video_id = str(item.get("video_id", "") or "")
            history_record = history_records.get(video_id)
            file_path = str(history_record.get("file_path", "") or "") if history_record else ""
            history_thumbnail_path = (
                str(history_record.get("thumbnail_path", "") or "") if history_record else ""
            )
            thumbnail_url = str(item.get("thumbnail_url", "") or "")
            cached_thumbnail_path = self._subscription_thumbnail_cache_path(video_id, thumbnail_url)
            task_status, task_error = task_info_by_video_id.get(video_id.lower(), ("", ""))
            download_state = str(item.get("download_state", "") or "")
            download_reason = str(item.get("download_reason", "") or "")
            if not download_state and task_error:
                inferred_state, inferred_reason = _subscription_download_block_from_error(task_error)
                if inferred_state:
                    download_state = inferred_state
                    download_reason = inferred_reason or task_error
                    self.subscriptions.update_item_download_state(video_id, download_state, download_reason)
            item["downloaded"] = bool(history_record)
            item["download_file_path"] = file_path
            item["download_file_exists"] = bool(file_path and os.path.exists(file_path))
            item["thumbnail_path"] = next(
                (
                    path
                    for path in (history_thumbnail_path, cached_thumbnail_path)
                    if path and os.path.isfile(path)
                ),
                "",
            )
            item["task_status"] = task_status
            item["queued"] = bool(task_status)
            item["download_state"] = download_state
            item["download_reason"] = download_reason
            item["downloadable"] = not bool(history_record) and not bool(task_status) and not download_state
        return items

    def remove_subscription_source(self, source_id: int):
        self.subscriptions.remove_source(source_id)

    def cache_subscription_thumbnail(self, video_id: str, thumbnail_url: str) -> str:
        """Cache a subscription cover and return its local path on success."""
        path = self._subscription_thumbnail_cache_path(video_id, thumbnail_url)
        if path and self._download_subscription_avatar(thumbnail_url, path):
            return path
        return ""

    def set_subscription_enabled(self, source_id: int, enabled: bool):
        self.subscriptions.set_source_enabled(source_id, enabled)

    def refresh_subscription_source_avatar(self, source_id: int) -> dict[str, Any]:
        source = self.subscriptions.get_source(source_id)
        if not source:
            return {
                "source_id": int(source_id),
                "avatar_url": "",
                "avatar_path": "",
                "error": tr("Subscription source does not exist", "订阅源不存在", "購読元が存在しません"),
            }
        if str(source.get("source_type", "") or "") != "author":
            return {
                "source_id": int(source_id),
                "avatar_url": "",
                "avatar_path": "",
                "error": "",
            }

        avatar_path = str(source.get("avatar_path", "") or "")
        avatar_url = str(source.get("avatar_url", "") or "")
        if avatar_path and os.path.isfile(avatar_path) and os.path.getsize(avatar_path) > 0:
            return {
                "source_id": int(source_id),
                "avatar_url": avatar_url,
                "avatar_path": avatar_path,
                "error": "",
            }

        if not avatar_url:
            username = str(source.get("source_key", "") or "").strip()
            profile, err = self._api_call("get_user_profile", username)
            if not profile:
                return {
                    "source_id": int(source_id),
                    "avatar_url": "",
                    "avatar_path": "",
                    "error": err,
                }
            user = _dict_or_empty(profile.get("user"))
            avatar = _dict_or_empty(user.get("avatar"))
            avatar_url = _iwara_image_url(avatar, variant="thumbnail")
            remote_id = str(user.get("id", "") or "").strip()
            if remote_id and not str(source.get("remote_id", "") or ""):
                self.subscriptions.update_source_remote_id(source_id, remote_id)
            if not avatar_url:
                self.subscriptions.update_source_avatar(source_id, "", "")
                return {
                    "source_id": int(source_id),
                    "avatar_url": "",
                    "avatar_path": "",
                    "error": "",
                }
            avatar_path = self._subscription_avatar_cache_path(source, avatar_url)
        else:
            avatar_path = avatar_path or self._subscription_avatar_cache_path(source, avatar_url)

        if self._download_subscription_avatar(avatar_url, avatar_path):
            self.subscriptions.update_source_avatar(source_id, avatar_url, avatar_path)
            return {
                "source_id": int(source_id),
                "avatar_url": avatar_url,
                "avatar_path": avatar_path,
                "error": "",
            }

        return {
            "source_id": int(source_id),
            "avatar_url": avatar_url,
            "avatar_path": "",
            "error": tr("Avatar download failed", "头像下载失败", "アバター保存に失敗しました"),
        }

    def mark_subscription_items_seen(self, video_ids: list[str]):
        self.subscriptions.mark_items_seen(video_ids)

    def mark_subscription_items_downloaded(self, video_ids: list[str]) -> int:
        ids = [str(v or "").strip() for v in video_ids if str(v or "").strip()]
        if not ids:
            return 0
        items = self.subscriptions.get_items_by_video_ids(ids)
        known = {str(item.get("video_id", "") or "") for item in items}
        for video_id in ids:
            if video_id not in known:
                items.append({"video_id": video_id, "source_url": f"https://www.iwara.tv/video/{video_id}"})
        marked = self.mark_video_items_downloaded(items)
        if marked:
            self.subscriptions.mark_items_seen(ids)
        return marked

    def restore_subscription_items_downloaded(self, video_ids: list[str]) -> int:
        ids = list(dict.fromkeys(str(v or "").strip() for v in video_ids if str(v or "").strip()))
        restored = 0
        for video_id in ids:
            record = self.history.get_record(video_id)
            if not record:
                continue
            file_path = str(record.get("file_path", "") or "")
            if file_path and os.path.exists(file_path):
                continue
            self.history.remove(video_id)
            restored += 1
        if restored:
            signal_bus.log_message.emit(
                tr(
                    f"[History] restored {restored} moved subscription videos",
                    f"[历史] 已还原 {restored} 个订阅视频的已移走状态",
                    f"[履歴] 移動済み状態を {restored} 件解除しました",
                )
            )
        return restored

    def mark_video_items_downloaded(self, items: list[dict[str, Any]]) -> int:
        marked = 0
        seen: set[str] = set()
        for item in items:
            video_id = str(item.get("video_id", "") or item.get("id", "") or "").strip()
            if not video_id or video_id.lower() in seen:
                continue
            seen.add(video_id.lower())
            existing = self.history.get_record(video_id, include_raw=True)
            meta = self._history_meta_from_item(item, existing)
            self.history.upsert_downloaded(meta)
            marked += 1
        if marked:
            signal_bus.log_message.emit(
                tr(
                    f"[History] marked {marked} videos as downloaded/moved",
                    f"[历史] 已标记 {marked} 个视频为已下载/已移走",
                    f"[履歴] {marked} 件を保存済み/移動済みとしてマーク",
                )
            )
        return marked

    def mark_subscription_source_seen(self, source_id: int):
        self.subscriptions.mark_source_seen(source_id)

    def enqueue_subscription_items(self, video_ids: list[str]) -> int:
        ids, _skipped = self._filter_downloadable_subscription_ids(video_ids)
        queued = self.enqueue_video_ids(ids, source_label=tr("Subscriptions", "订阅页", "購読"))
        self.mark_subscription_items_seen(ids)
        return queued

    def submit_subscription_items(self, video_ids: list[str]) -> dict[str, int | str]:
        ids = list(dict.fromkeys(str(v or "").strip() for v in video_ids if str(v or "").strip()))
        ids, skipped_unavailable = self._filter_downloadable_subscription_ids(ids)
        if not ids:
            return {
                "mode": "empty",
                "queued": 0,
                "marked": 0,
                "thumbnail": 0,
                "nfo": 0,
                "failed": 0,
                "skipped_unavailable": skipped_unavailable,
            }
        if app_config.download_video_file and not app_config.mark_submitted_as_downloaded:
            queued = self.enqueue_subscription_items(ids)
            return {
                "mode": "download",
                "queued": queued,
                "marked": 0,
                "thumbnail": 0,
                "nfo": 0,
                "failed": 0,
                "skipped_unavailable": skipped_unavailable,
            }
        result = self._process_subscription_items_metadata_only(ids)
        result["skipped_unavailable"] = skipped_unavailable
        return result

    def _filter_downloadable_subscription_ids(self, video_ids: list[str]) -> tuple[list[str], int]:
        ids = list(dict.fromkeys(str(v or "").strip() for v in video_ids if str(v or "").strip()))
        if not ids:
            return [], 0
        blocked: set[str] = set()
        for item in self.subscriptions.get_items_by_video_ids(ids):
            video_id = str(item.get("video_id", "") or "")
            if video_id and str(item.get("download_state", "") or "") == _SUBSCRIPTION_UNAVAILABLE_STATE:
                blocked.add(video_id)
        return [video_id for video_id in ids if video_id not in blocked], len(blocked)

    def _process_subscription_items_metadata_only(self, video_ids: list[str]) -> dict[str, int | str]:
        items = self.subscriptions.get_items_by_video_ids(video_ids)
        fallback_by_id = {
            str(item.get("video_id", "") or ""): item
            for item in items
            if str(item.get("video_id", "") or "")
        }
        result: dict[str, int | str] = {
            "mode": "metadata",
            "queued": 0,
            "marked": 0,
            "thumbnail": 0,
            "nfo": 0,
            "failed": 0,
        }
        for video_id in video_ids:
            source_url = str(fallback_by_id.get(video_id, {}).get("source_url", "") or f"https://www.iwara.tv/video/{video_id}")
            video_info, err = self._api_call("get_video_info", video_id)
            if not video_info:
                download_state, download_reason = _subscription_download_block_from_error(err)
                if download_state:
                    self.subscriptions.update_item_download_state(video_id, download_state, download_reason or err)
                    result["failed"] = int(result["failed"]) + 1
                    signal_bus.log_message.emit(
                        tr(
                            f"[Subscriptions] metadata unavailable for {video_id}: {download_reason or err}",
                            f"[订阅] {video_id} 元数据不可下载：{download_reason or err}",
                            f"[購読] {video_id} のメタデータ保存不可: {download_reason or err}",
                        )
                    )
                    continue
                fallback = fallback_by_id.get(video_id) or {
                    "video_id": video_id,
                    "source_url": source_url,
                }
                result["marked"] = int(result["marked"]) + self.mark_video_items_downloaded([fallback])
                result["failed"] = int(result["failed"]) + 1
                signal_bus.log_message.emit(
                    tr(
                        f"[Subscriptions] metadata fetch failed for {video_id}, marked from cached row: {err}",
                        f"[订阅] {video_id} 元数据获取失败，已按缓存行标记：{err}",
                        f"[購読] {video_id} のメタデータ取得失敗、キャッシュ行で記録: {err}",
                    )
                )
                continue

            unavailable_reason = self._video_unavailable_reason(video_info)
            if unavailable_reason:
                download_state, download_reason = _subscription_download_block_from_video_info(video_info)
                if download_state:
                    self.subscriptions.update_item_download_state(video_id, download_state, download_reason or unavailable_reason)
                    result["failed"] = int(result["failed"]) + 1
                    signal_bus.log_message.emit(
                        tr(
                            f"[Subscriptions] metadata unavailable for {video_id}: {download_reason or unavailable_reason}",
                            f"[订阅] {video_id} 元数据不可下载：{download_reason or unavailable_reason}",
                            f"[購読] {video_id} のメタデータ保存不可: {download_reason or unavailable_reason}",
                        )
                    )
                    continue
                fallback = fallback_by_id.get(video_id) or {
                    "video_id": video_id,
                    "source_url": source_url,
                }
                result["marked"] = int(result["marked"]) + self.mark_video_items_downloaded([fallback])
                result["failed"] = int(result["failed"]) + 1
                signal_bus.log_message.emit(
                    tr(
                        f"[Subscriptions] metadata unavailable for {video_id}: {unavailable_reason}",
                        f"[订阅] {video_id} 元数据不可用：{unavailable_reason}",
                        f"[購読] {video_id} のメタデータ利用不可: {unavailable_reason}",
                    )
                )
                continue

            task = self._metadata_task_from_video_info(video_id, video_info, source_url)
            if app_config.download_thumbnail and self._download_thumbnail(task, require_video_file=False):
                result["thumbnail"] = int(result["thumbnail"]) + 1
            if app_config.collect_nfo_info and self._write_nfo(task, require_video_file=False):
                result["nfo"] = int(result["nfo"]) + 1

            existing = self.history.get_record(task.video_id, include_raw=True)
            history_item = dict(video_info)
            history_item["video_id"] = task.video_id
            history_item["source_url"] = source_url
            meta = self._history_meta_from_item(history_item, existing)
            meta["thumbnail_path"] = task.thumbnail_path or meta.get("thumbnail_path", "")
            self.history.upsert_downloaded(meta)
            result["marked"] = int(result["marked"]) + 1

        if int(result["marked"]):
            self.subscriptions.mark_items_seen(video_ids)
            signal_bus.log_message.emit(
                tr(
                    f"[Subscriptions] metadata-only done: marked={result['marked']}, thumbnails={result['thumbnail']}, nfo={result['nfo']}, failed={result['failed']}",
                    f"[订阅] 仅元数据处理完成：标记={result['marked']}，封面={result['thumbnail']}，NFO={result['nfo']}，失败={result['failed']}",
                    f"[購読] メタデータのみ完了: 記録={result['marked']}、サムネイル={result['thumbnail']}、NFO={result['nfo']}、失敗={result['failed']}",
                )
            )
        return result

    def refresh_all_subscriptions(
        self,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Refresh enabled sources and optionally report source-level progress."""
        summaries: list[dict[str, Any]] = []
        sources = [
            source
            for source in self.get_subscription_sources()
            if int(source.get("enabled", 1) or 0)
        ]
        total = len(sources)
        for index, source in enumerate(sources, start=1):
            source_id = int(source["id"])
            source_title = str(source.get("title", "") or source.get("source_key", "") or "")
            if progress_callback:
                progress_callback(
                    {
                        "stage": "started",
                        "index": index,
                        "total": total,
                        "source_id": source_id,
                        "title": source_title,
                    }
                )
            summary = self.refresh_subscription_source(source_id)
            summaries.append(summary)
            if progress_callback:
                progress_callback(
                    {
                        "stage": "finished",
                        "index": index,
                        "total": total,
                        "source_id": source_id,
                        "title": str(summary.get("title", "") or source_title),
                        "summary": summary,
                    }
                )
        return self._subscription_refresh_summary(summaries)

    def refresh_subscription_source(self, source_id: int) -> dict[str, Any]:
        source = self.subscriptions.get_source(source_id)
        if not source:
            return {
                "source_id": source_id,
                "title": "",
                "new": 0,
                "total": 0,
                "downloaded": 0,
                "fetched": 0,
                "error": tr("Subscription source does not exist", "订阅源不存在", "購読元が存在しません"),
            }
        if not int(source.get("enabled", 1) or 0):
            return {
                "source_id": source_id,
                "title": str(source.get("title", "") or ""),
                "new": 0,
                "total": self.subscriptions.count_items(source_id),
                "downloaded": 0,
                "fetched": 0,
                "error": tr("Subscription source is disabled", "订阅源已停用", "購読元は無効です"),
            }

        source_type = str(source.get("source_type", "") or "")
        source_key = str(source.get("source_key", "") or "")
        title = str(source.get("title", "") or source_key)
        videos: list[dict] = []
        err = ""
        cap = self._subscription_fetch_limit()

        if source_type == "feed":
            videos, err = self._api_call("get_subscribed_videos", max_results=cap)
        elif source_type == "author":
            remote_id = str(source.get("remote_id", "") or "")
            avatar_url = str(source.get("avatar_url", "") or "")
            # Refresh also repairs older/manual author sources that lack a
            # display name, remote ID, or avatar URL.
            needs_profile = not remote_id or not avatar_url or title == source_key
            if needs_profile:
                try:
                    profile, _profile_err = self._api_call("get_user_profile", source_key)
                except Exception:
                    profile = None
                if profile:
                    user = _dict_or_empty(profile.get("user"))
                    profile_title = str(user.get("name", "") or source_key).strip() or source_key
                    remote_id = str(user.get("id", "") or remote_id).strip()
                    avatar_url = _iwara_image_url(_dict_or_empty(user.get("avatar")), variant="thumbnail") or avatar_url
                    self.subscriptions.update_source_profile(
                        source_id,
                        title=profile_title,
                        remote_id=remote_id,
                        avatar_url=avatar_url,
                    )
                    title = profile_title
            if not remote_id and not err:
                remote_id, err = self._api_call("get_user_id", source_key)
                if remote_id:
                    self.subscriptions.update_source_remote_id(source_id, remote_id)
            if remote_id:
                videos = self._api_call("get_user_videos", remote_id)
        elif source_type == "playlist":
            videos = self._api_call("get_playlist_videos", source_key)
        else:
            err = tr(
                f"Unknown subscription type: {source_type}",
                f"未知订阅类型: {source_type}",
                f"不明な購読タイプ: {source_type}",
            )

        if err:
            self.subscriptions.touch_source_checked(source_id)
            return {
                "source_id": source_id,
                "title": title,
                "new": 0,
                "total": self.subscriptions.count_items(source_id),
                "downloaded": 0,
                "fetched": len(videos),
                "error": err,
            }

        normalized_items = [_subscription_item_from_video(video) for video in videos]
        new_count, total_count = self.subscriptions.upsert_items(source_id, normalized_items)
        unavailable_checked = self._validate_subscription_unavailable_items(normalized_items)
        self.subscriptions.touch_source_checked(source_id)
        items = self.get_subscription_items(source_id)
        downloaded_count = sum(1 for item in items if item.get("downloaded"))
        unavailable_count = sum(1 for item in items if str(item.get("download_state", "") or "") == _SUBSCRIPTION_UNAVAILABLE_STATE)
        return {
            "source_id": source_id,
            "title": title,
            "new": new_count,
            "total": total_count,
            "downloaded": downloaded_count,
            "unavailable": unavailable_count,
            "unavailable_checked": unavailable_checked,
            "fetched": len(videos),
            "error": "",
        }

    def _validate_subscription_unavailable_items(self, items: list[dict[str, Any]]) -> int:
        ids = list(
            dict.fromkeys(
                str(item.get("video_id", "") or "").strip()
                for item in items
                if str(item.get("video_id", "") or "").strip()
            )
        )
        if not ids:
            return 0
        history_records = self.history.get_records(ids)
        with self._lock:
            active_video_ids = {task.video_id.lower() for task in self._tasks.values() if task.video_id}

        candidates: list[str] = []
        for item in self.subscriptions.get_items_by_video_ids(ids):
            video_id = str(item.get("video_id", "") or "").strip()
            if not video_id or video_id in history_records or video_id.lower() in active_video_ids:
                continue
            download_state = str(item.get("download_state", "") or "")
            checked_at = str(item.get("download_checked_at", "") or "")
            if download_state == _SUBSCRIPTION_UNAVAILABLE_STATE or not checked_at:
                candidates.append(video_id)

        checked = 0
        for video_id in candidates:
            video_info, err = self._api_call("get_video_info", video_id)
            if not video_info:
                download_state, download_reason = _subscription_download_block_from_error(err)
                if download_state:
                    self.subscriptions.update_item_download_state(video_id, download_state, download_reason or err)
                    checked += 1
                continue

            download_state, download_reason = _subscription_download_block_from_video_info(video_info)
            if download_state:
                self.subscriptions.update_item_download_state(video_id, download_state, download_reason)
                checked += 1
                continue
            if str(video_info.get("fileUrl", "") or ""):
                self.subscriptions.update_item_download_state(video_id, "", "")
                checked += 1
        return checked

    @staticmethod
    def _subscription_refresh_summary(summaries: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "sources": len(summaries),
            "new": sum(int(s.get("new", 0) or 0) for s in summaries),
            "total": sum(int(s.get("total", 0) or 0) for s in summaries),
            "downloaded": sum(int(s.get("downloaded", 0) or 0) for s in summaries),
            "unavailable": sum(int(s.get("unavailable", 0) or 0) for s in summaries),
            "unavailable_checked": sum(int(s.get("unavailable_checked", 0) or 0) for s in summaries),
            "errors": [s for s in summaries if s.get("error")],
            "details": summaries,
        }

    @staticmethod
    def _subscription_fetch_limit() -> int:
        if not app_config.search_limit_enabled:
            return 0
        return max(1, int(app_config.search_limit_count or 100))

    def _add_author_sources_from_users(self, users: list[dict]) -> int:
        imported = 0
        for entry in users:
            user = entry.get("user") if isinstance(entry, dict) else None
            if not isinstance(user, dict):
                user = entry if isinstance(entry, dict) else {}
            username = str(user.get("username") or "").strip()
            if not username:
                continue
            title = str(user.get("name") or username).strip()
            remote_id = str(user.get("id") or "").strip()
            avatar_url = _iwara_image_url(_dict_or_empty(user.get("avatar")), variant="thumbnail")
            if self.subscriptions.add_source("author", username, title, remote_id, avatar_url=avatar_url):
                imported += 1
        return imported

    def _add_author_sources_from_videos(self, videos: list[dict]) -> int:
        users_by_name: dict[str, dict] = {}
        for video in videos:
            user = video.get("user")
            if not isinstance(user, dict):
                continue
            username = str(user.get("username") or "").strip()
            if username:
                users_by_name[username.lower()] = user
        return self._add_author_sources_from_users(list(users_by_name.values()))

    def _history_meta_from_item(
        self, item: dict[str, Any], existing: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        video_id = str(item.get("video_id", "") or item.get("id", "") or "").strip()
        user = _dict_or_empty(item.get("user"))
        file_info = _dict_or_empty(item.get("file"))

        def value(*keys: str, default: Any = "") -> Any:
            for key in keys:
                raw = item.get(key)
                if raw not in (None, ""):
                    return raw
            if existing:
                for key in keys:
                    raw = existing.get(key)
                    if raw not in (None, ""):
                        return raw
            return default

        author = str(value("author", default="") or "")
        if not author:
            author = str(user.get("username") or user.get("name") or "")

        raw_tags = item.get("tags", [])
        tags_json = json.dumps(raw_tags, ensure_ascii=False) if isinstance(raw_tags, list) else ""
        raw_json = _compact_video_raw_json(item) if "user" in item or "body" in item else ""
        if existing:
            tags_json = tags_json or str(existing.get("tags_json", "") or "")
            raw_json = raw_json or str(existing.get("raw_json", "") or "")
        return {
            "video_id": video_id,
            "title": str(value("title", default=video_id) or video_id),
            "author": author,
            "published_at": str(value("published_at", "createdAt", "updatedAt", default="") or ""),
            "likes": int(value("likes", "numLikes", default=0) or 0),
            "views": int(value("views", "numViews", default=0) or 0),
            "slug": str(value("slug", default="") or ""),
            "rating": str(value("rating", default="") or ""),
            "duration": int(value("duration", default=file_info.get("duration", 0) or 0) or 0),
            "comments": int(value("comments", "numComments", default=0) or 0),
            "tags_json": tags_json,
            "raw_json": raw_json,
            "source_url": str(value("source_url", default=f"https://www.iwara.tv/video/{video_id}") or ""),
            "file_path": str(existing.get("file_path", "") or "") if existing else "",
            "thumbnail_path": str(existing.get("thumbnail_path", "") or "") if existing else "",
            "quality": str(existing.get("quality", "") or "") if existing else "",
        }

    def _metadata_task_from_video_info(
        self, video_id: str, video_info: dict[str, Any], source_url: str
    ) -> DownloadTask:
        user = _dict_or_empty(video_info.get("user"))
        file_info = _dict_or_empty(video_info.get("file"))
        task_video_id = str(video_info.get("id", "") or video_info.get("video_id", "") or video_id)
        title = str(video_info.get("title", "") or task_video_id)
        author = str(user.get("username", "") or user.get("name", "") or "")
        published_at = str(video_info.get("createdAt", "") or "")
        likes = int(video_info.get("numLikes", 0) or 0)
        views = int(video_info.get("numViews", 0) or 0)
        slug = str(video_info.get("slug", "") or "")
        rating = str(video_info.get("rating", "") or "")
        duration = int(file_info.get("duration", 0) or 0)
        comments = int(video_info.get("numComments", 0) or 0)
        raw_tags = video_info.get("tags", [])
        tags_json = json.dumps(raw_tags, ensure_ascii=False) if isinstance(raw_tags, list) else ""
        raw_json = _compact_video_raw_json(video_info)
        file_url = str(video_info.get("fileUrl", "") or "")
        file_id = str(file_info.get("id", "") or "")
        thumbnail_index = int(video_info.get("thumbnail", 0) or 0)
        quality = str(app_config.preferred_quality or "metadata")

        task = DownloadTask(str(uuid.uuid4()), source_url, task_video_id)
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
        task.quality = quality
        output_rel_path = self._build_output_relative_path(
            title=title,
            video_id=task_video_id,
            author=author,
            published_at=published_at,
            quality=quality,
            likes=likes,
            views=views,
            comments=comments,
            duration=duration,
            slug=slug,
            rating=rating,
        )
        task.file_path = os.path.join(app_config.download_dir, output_rel_path)
        task.filename = os.path.basename(task.file_path)
        return task

    def open_history_output(
        self, video_id: str, *, open_file: bool = False
    ) -> tuple[bool, str]:
        record = self.history.get_record(video_id)
        if not record:
            return False, tr("History record does not exist", "历史记录不存在", "履歴が存在しません")

        file_path = str(record.get("file_path", "") or "")
        if not file_path or not os.path.exists(file_path):
            return False, tr("File does not exist", "文件不存在", "ファイルが存在しません")

        target = file_path if open_file else os.path.dirname(file_path)
        return self._open_system_path(target)

    def rename_history_file(
        self, video_id: str, new_filename: str
    ) -> tuple[bool, str]:
        record = self.history.get_record(video_id)
        if not record:
            return False, tr("History record does not exist", "历史记录不存在", "履歴が存在しません")

        old_path = str(record.get("file_path", "") or "")
        if not old_path or not os.path.isfile(old_path):
            return False, tr("File does not exist", "文件不存在", "ファイルが存在しません")

        old_dir = os.path.dirname(old_path)
        old_ext = os.path.splitext(old_path)[1] or ".mp4"
        cleaned_name = self._sanitize_path_segment(new_filename.strip())
        if cleaned_name in ("", "_"):
            return False, tr("Invalid file name", "文件名无效", "ファイル名が不正です")
        if not os.path.splitext(cleaned_name)[1]:
            cleaned_name += old_ext

        new_path = os.path.join(old_dir, cleaned_name)
        if os.path.abspath(new_path) == os.path.abspath(old_path):
            return False, tr("File name is unchanged", "文件名没有变化", "ファイル名は変更されていません")
        if os.path.exists(new_path):
            return False, tr("Target file already exists", "目标文件已存在", "変更先ファイルは既に存在します")

        old_stem = os.path.splitext(old_path)[0]
        new_stem = os.path.splitext(new_path)[0]
        sidecars: list[tuple[str, str]] = []

        old_thumbnail = str(record.get("thumbnail_path", "") or "")
        if old_thumbnail and os.path.isfile(old_thumbnail):
            thumb_ext = os.path.splitext(old_thumbnail)[1] or ".jpg"
            sidecars.append((old_thumbnail, new_stem + thumb_ext))

        nfo_path = old_stem + ".nfo"
        if os.path.isfile(nfo_path):
            sidecars.append((nfo_path, new_stem + ".nfo"))

        for _, target in sidecars:
            if os.path.exists(target):
                return False, tr(
                    f"Sidecar target already exists: {target}",
                    f"同名附属文件已存在: {target}",
                    f"関連ファイルの変更先が既に存在します: {target}",
                )

        new_thumbnail = old_thumbnail
        try:
            os.rename(old_path, new_path)
            for source, target in sidecars:
                os.rename(source, target)
                if source == old_thumbnail:
                    new_thumbnail = target
        except Exception as exc:
            return False, str(exc)

        self.history.update_file_paths(
            video_id,
            file_path=new_path,
            thumbnail_path=new_thumbnail if os.path.exists(new_thumbnail) else "",
        )
        signal_bus.log_message.emit(
            tr(
                f"[History] Renamed file: {old_path} -> {new_path}",
                f"[历史] 已重命名文件: {old_path} -> {new_path}",
                f"[履歴] ファイル名を変更しました: {old_path} -> {new_path}",
            )
        )
        return True, new_path

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

    def _open_system_path(self, target: str) -> tuple[bool, str]:
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
        return True, ""

    def set_login(self, logged_in: bool, token: str | None = None):
        with self._api_lock:
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
        with self._api_lock:
            self.api.token = token
        return True

    def apply_config(self):
        """Apply proxy settings from app_config to the scraper."""
        with self._api_lock:
            if app_config.api_proxy_enabled and app_config.api_proxy_url:
                self.api.set_proxy(app_config.api_proxy_url)
            else:
                self.api.set_proxy("")

    def _download_request_proxies(self) -> dict[str, str | None]:
        proxy_url = (app_config.download_proxy_url or "").strip()
        if app_config.download_proxy_enabled and proxy_url:
            return {"http": proxy_url, "https": proxy_url}
        return {"http": None, "https": None}

    def _api_call(self, method_name: str, *args, **kwargs):
        """Serialize access to the shared cloudscraper session."""
        with self._api_lock:
            method = getattr(self.api, method_name)
            return method(*args, **kwargs)

    def _current_token(self) -> str:
        with self._api_lock:
            return self.api.token or ""

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

    def _parse_and_mark_downloaded(self, raw: str):
        url = raw.strip()
        if not url:
            return
        signal_bus.log_message.emit(
            tr(
                f"[Mark downloaded] parsing input: {url}",
                f"[标记已下载] 解析输入：{url}",
                f"[保存済みマーク] 入力を解析中: {url}",
            )
        )

        parsed = self._parse_iwara_url(url)
        items: list[dict[str, Any]] = []
        if parsed:
            kind, value = parsed
            if kind == "video":
                video_info, err = self._api_call("get_video_info", str(value))
                if video_info:
                    items = [video_info]
                else:
                    signal_bus.log_message.emit(
                        tr(
                            f"[Mark downloaded] video info failed, fallback to id only: {err}",
                            f"[标记已下载] 获取视频信息失败，退回为仅按 ID 标记：{err}",
                            f"[保存済みマーク] 動画情報取得失敗、IDのみで記録: {err}",
                        )
                    )
                    items = [{"video_id": str(value), "source_url": url}]
            elif kind == "user":
                user_id, err = self._api_call("get_user_id", str(value))
                if not user_id:
                    signal_bus.log_message.emit(
                        tr(
                            f"[Mark downloaded] failed to get user id: {err}",
                            f"[标记已下载] 无法获取用户 ID：{err}",
                            f"[保存済みマーク] ユーザーID取得失敗: {err}",
                        )
                    )
                    return
                items = self._api_call("get_user_videos", user_id)
            elif kind == "playlist":
                items = self._api_call("get_playlist_videos", str(value))
            elif kind == "search":
                configured_cap = (
                    max(0, int(app_config.search_limit_count))
                    if app_config.search_limit_enabled
                    else 0
                )
                videos, err = self._api_call(
                    "get_videos_by_query",
                    value,
                    max_results=configured_cap,
                )
                if err:
                    signal_bus.log_message.emit(
                        tr(
                            f"[Mark downloaded] search returned partial result: {err}",
                            f"[标记已下载] 搜索返回部分结果：{err}",
                            f"[保存済みマーク] 検索は部分結果を返しました: {err}",
                        )
                    )
                items = videos
        else:
            video_info, err = self._api_call("get_video_info", url)
            items = [video_info] if video_info else [{"video_id": url, "source_url": url}]
            if err and not video_info:
                signal_bus.log_message.emit(
                    tr(
                        f"[Mark downloaded] video info failed, fallback to id only: {err}",
                        f"[标记已下载] 获取视频信息失败，退回为仅按 ID 标记：{err}",
                        f"[保存済みマーク] 動画情報取得失敗、IDのみで記録: {err}",
                    )
                )

        marked = self.mark_video_items_downloaded(items)
        signal_bus.log_message.emit(
            tr(
                f"[Mark downloaded] done: {marked} videos",
                f"[标记已下载] 完成：{marked} 个视频",
                f"[保存済みマーク] 完了: {marked} 件",
            )
        )

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
        self._enqueue_video_ids_bulk([(video_id, original_url)])

    def _enqueue_video_ids_bulk(
        self,
        items: list[tuple[str, str]],
        *,
        source_label: str = "",
    ) -> dict[str, int]:
        seen_input: set[str] = set()
        normalized: list[tuple[str, str]] = []
        skipped_empty = 0
        skipped_input_duplicate = 0
        for raw_video_id, original_url in items:
            video_id = str(raw_video_id or "").strip()
            if not video_id:
                skipped_empty += 1
                continue
            key = video_id.lower()
            if key in seen_input:
                skipped_input_duplicate += 1
                continue
            seen_input.add(key)
            normalized.append((video_id, original_url or f"https://www.iwara.tv/video/{video_id}"))

        existing_paths: dict[str, str] = {}
        if app_config.skip_existing_files and normalized:
            history_records = self.history.get_records([video_id for video_id, _ in normalized])
            file_index = self._get_existing_file_index()
            for video_id, _ in normalized:
                record = history_records.get(video_id)
                if record:
                    file_path = str(record.get("file_path", "") or "")
                    if file_path and os.path.isfile(file_path) and os.path.getsize(file_path) > 0:
                        existing_paths[video_id.lower()] = file_path
                        continue
                existing = file_index.get(video_id.lower())
                if existing and os.path.isfile(existing):
                    existing_paths[video_id.lower()] = existing

        added_infos: list[dict[str, Any]] = []
        skipped_existing = len(existing_paths)
        skipped_duplicate = 0
        queued_count = 0
        active_count = 0
        limit = app_config.max_concurrent
        with self._lock:
            for video_id, original_url in normalized:
                key = video_id.lower()
                if key in existing_paths:
                    continue
                if key in self._task_id_by_video_id:
                    skipped_duplicate += 1
                    continue
                task_id = str(uuid.uuid4())
                task = DownloadTask(task_id=task_id, url=original_url, video_id=video_id)
                self._tasks[task_id] = task
                self._task_id_by_video_id[key] = task_id
                self._queued_meta_ids.append(task_id)
                added_infos.append(
                    {
                        "task_id": task_id,
                        "video_id": video_id,
                        "title": video_id,
                        "author": "",
                        "status": TaskStatus.QUEUED_META.value,
                    }
                )
            queued_count = len(self._queued_meta_ids)
            active_count = len(self._active_task_ids)

        if added_infos:
            signal_bus.tasks_added.emit(added_infos)
            if len(added_infos) == 1:
                info = dict(added_infos[0])
                task_id = str(info.pop("task_id"))
                signal_bus.task_added.emit(task_id, info)

        label = f" from {source_label}" if source_label else ""
        signal_bus.log_message.emit(
            tr(
                f"[Queue] added {len(added_infos)}{label}; duplicate {skipped_duplicate + skipped_input_duplicate}; existing {skipped_existing}; queued {queued_count}; active {active_count}/{limit}",
                f"[入队] 新增 {len(added_infos)} 个{('，来源: ' + source_label) if source_label else ''}；重复 {skipped_duplicate + skipped_input_duplicate}；本地已存在 {skipped_existing}；排队 {queued_count}；活动 {active_count}/{limit}",
                f"[キュー] 追加 {len(added_infos)} 件{label}; 重複 {skipped_duplicate + skipped_input_duplicate}; 既存 {skipped_existing}; 待機 {queued_count}; 稼働 {active_count}/{limit}",
            )
        )
        self._try_activate()
        return {
            "queued": len(added_infos),
            "duplicates": skipped_duplicate + skipped_input_duplicate,
            "existing": skipped_existing,
            "empty": skipped_empty,
        }

    def _enqueue_user(self, username: str):
        signal_bus.log_message.emit(
            tr(
                f"Fetching videos for user [{username}] ...",
                f"正在获取用户 [{username}] 的视频列表…",
                f"ユーザー [{username}] の動画一覧を取得中...",
            )
        )
        user_id, err = self._api_call("get_user_id", username)
        if not user_id:
            signal_bus.log_message.emit(
                tr(
                    f"[Error] Failed to get user ID: {err}",
                    f"[错误] 无法获取用户 ID: {err}",
                    f"[エラー] ユーザーIDの取得に失敗: {err}",
                )
            )
            return
        videos = self._api_call("get_user_videos", user_id)
        signal_bus.log_message.emit(
            tr(
                f"Found {len(videos)} videos for user [{username}]",
                f"用户 [{username}] 共找到 {len(videos)} 个视频",
                f"ユーザー [{username}] で {len(videos)} 件の動画を検出",
            )
        )
        self._enqueue_video_ids_bulk(
            [
                (str(video.get("id", "") or "").strip(), f"https://www.iwara.tv/video/{str(video.get('id', '') or '').strip()}")
                for video in videos
                if str(video.get("id", "") or "").strip()
            ],
            source_label=username,
        )

    def _enqueue_playlist(self, playlist_id: str):
        signal_bus.log_message.emit(
            tr(
                f"Fetching videos from playlist [{playlist_id}] ...",
                f"正在获取播放列表 [{playlist_id}] 的视频…",
                f"プレイリスト [{playlist_id}] の動画を取得中...",
            )
        )
        videos = self._api_call("get_playlist_videos", playlist_id)
        signal_bus.log_message.emit(
            tr(
                f"Found {len(videos)} videos in playlist",
                f"播放列表共找到 {len(videos)} 个视频",
                f"プレイリストで {len(videos)} 件の動画を検出",
            )
        )
        self._enqueue_video_ids_bulk(
            [
                (str(video.get("id", "") or "").strip(), f"https://www.iwara.tv/video/{str(video.get('id', '') or '').strip()}")
                for video in videos
                if str(video.get("id", "") or "").strip()
            ],
            source_label=tr("Playlist", "播放列表", "プレイリスト"),
        )

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

        videos, err = self._api_call(
            "get_videos_by_query",
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
        self._enqueue_video_ids_bulk(
            [
                (str(video.get("id", "") or "").strip(), f"https://www.iwara.tv/video/{str(video.get('id', '') or '').strip()}")
                for video in videos
                if str(video.get("id", "") or "").strip()
            ],
            source_label=tr("Search", "搜索", "検索"),
        )

    # ── Scheduler ─────────────────────────────────────────────────────────────

    def _count_active(self) -> int:
        """Must be called with self._lock held."""
        return len(self._active_task_ids)

    def _try_activate(self):
        """Promote QUEUED_META tasks into RESOLVING up to the concurrency limit."""
        to_resolve: list[str] = []
        with self._lock:
            active = self._count_active()
            limit = app_config.max_concurrent
            while active < limit and self._queued_meta_ids:
                task_id = self._queued_meta_ids.popleft()
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

    # ── Resolution stage ──────────────────────────────────────────────────────

    def _resolve_task(self, task_id: str):
        try:
            self._resolve_task_impl(task_id)
        except Exception as exc:
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
        author: str = user_info.get("username", "") or ""
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
        token = self._current_token()
        if token:
            headers.append(f"Authorization: Bearer {token}")

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
        if app_config.download_proxy_enabled and app_config.download_proxy_url:
            options["all-proxy"] = app_config.download_proxy_url

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

        with self._lock:
            task.aria2_gid = gid
            self._touch_task_activity_locked(task_id)
        if self._is_cancel_requested(task_id):
            self._aria2_rpc_cancel(gid)
            self._cancel_task_terminal(
                task_id,
                tr("Cancelled by user", "用户已中断", "ユーザーが中断しました"),
            )
            return

        last_emit = 0.0
        last_done = -1
        last_status = ""
        while True:
            if self._is_cancel_requested(task_id):
                self._aria2_rpc_cancel(gid)
                self._cancel_task_terminal(
                    task_id,
                    tr("Cancelled by user", "用户已中断", "ユーザーが中断しました"),
                )
                return
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
            if status != last_status or done > last_done or speed > 0:
                self._touch_task_activity(task_id)
                last_status = status
                last_done = max(last_done, done)

            now = time.monotonic()
            if now - last_emit >= 0.5:
                with self._lock:
                    task.downloaded_bytes = done
                    task.total_bytes = total
                    task.speed_str = speed_str
                signal_bus.task_progress_updated.emit(task_id, done, total, speed_str)
                last_emit = now

            if status == "complete":
                if self._is_cancel_requested(task_id):
                    self._aria2_rpc_cancel(gid)
                    self._cancel_task_terminal(
                        task_id,
                        tr("Cancelled by user", "用户已中断", "ユーザーが中断しました"),
                    )
                    return
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
                if self._is_cancel_requested(task_id):
                    self._cancel_task_terminal(
                        task_id,
                        tr("Cancelled by user", "用户已中断", "ユーザーが中断しました"),
                    )
                    self._aria2_rpc_remove_result(gid)
                    return
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
        finally:
            if resp is not None:
                try:
                    resp.close()
                except Exception:
                    pass

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
        parts = self._fit_output_path_to_windows_limit(parts)
        return os.path.join(*parts)

    @classmethod
    def _sanitize_path_segment(cls, name: str) -> str:
        """Return a portable, Windows-safe single path segment."""
        cleaned = re.sub(r'[\x00-\x1f\\/:*?"<>|\x7f]', "-", str(name)).strip(" .")
        if cleaned in ("", ".", ".."):
            return "_"
        stem = cleaned.split(".", 1)[0].rstrip(" ").upper()
        if stem in _WINDOWS_RESERVED_FILENAMES:
            cleaned = f"_{cleaned}"
        return cls._shorten_path_segment(cleaned, _WINDOWS_SAFE_PATH_LIMIT)

    @staticmethod
    def _windows_path_length(path: str) -> int:
        """Count UTF-16 code units, the length Windows uses for paths."""
        return len(path.encode("utf-16-le")) // 2

    @classmethod
    def _shorten_path_segment(cls, name: str, max_length: int) -> str:
        """Shorten a segment while retaining both its beginning and ending."""
        if cls._windows_path_length(name) <= max_length:
            return name

        stem, extension = os.path.splitext(name)
        digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:10]
        marker = f"-{digest}-"
        available = max(1, max_length - cls._windows_path_length(extension) - cls._windows_path_length(marker))
        prefix_length = max(1, available * 2 // 5)
        suffix_length = max(1, available - prefix_length)
        prefix = cls._trim_to_windows_length(stem, prefix_length)
        suffix = cls._trim_to_windows_length(stem, suffix_length, from_end=True)
        return f"{prefix}{marker}{suffix}{extension}"

    @classmethod
    def _fit_output_path_to_windows_limit(self, parts: list[str]) -> list[str]:
        """Keep output paths usable by Windows and its temporary download files."""
        result = list(parts)
        base_dir = os.path.abspath(app_config.download_dir)
        while self._windows_path_length(os.path.join(base_dir, *result)) > _WINDOWS_SAFE_PATH_LIMIT:
            candidates = [
                (self._windows_path_length(part), index)
                for index, part in enumerate(result)
                if self._windows_path_length(part) > _WINDOWS_MIN_PATH_SEGMENT_LENGTH
            ]
            if not candidates:
                break
            _, index = max(candidates)
            current_length = self._windows_path_length(result[index])
            excess = self._windows_path_length(os.path.join(base_dir, *result)) - _WINDOWS_SAFE_PATH_LIMIT
            target_length = max(_WINDOWS_MIN_PATH_SEGMENT_LENGTH, current_length - excess)
            result[index] = self._shorten_path_segment(result[index], target_length)
        return result

    @staticmethod
    def _trim_to_windows_length(text: str, max_length: int, *, from_end: bool = False) -> str:
        chars = reversed(text) if from_end else iter(text)
        kept: list[str] = []
        length = 0
        for char in chars:
            char_length = DownloadManager._windows_path_length(char)
            if length + char_length > max_length:
                break
            kept.append(char)
            length += char_length
        if from_end:
            kept.reverse()
        return "".join(kept)

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
        title: str,
        likes: int,
        views: int,
        published_at: str,
        tags: list[Any],
    ) -> tuple[bool, str]:
        normalized_title = str(title or "").casefold()
        title_include_terms = _split_filter_tags(app_config.filter_title_include)
        if title_include_terms and not any(term in normalized_title for term in title_include_terms):
            return False, tr(
                f"title did not include any of: {', '.join(title_include_terms)}",
                f"标题未包含任一关键词：{', '.join(title_include_terms)}",
                f"タイトルに指定語句が含まれません：{', '.join(title_include_terms)}",
            )

        title_exclude_terms = _split_filter_tags(app_config.filter_title_exclude)
        if title_exclude_terms:
            hit = [term for term in title_exclude_terms if term in normalized_title]
            if hit:
                return False, tr(
                    f"title matched exclude keywords: {', '.join(hit)}",
                    f"标题命中排除关键词：{', '.join(hit)}",
                    f"タイトルが除外語句に一致：{', '.join(hit)}",
                )

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

        resp = None
        try:
            resp = self.api.scraper.post(
                rpc_url,
                json=payload,
                timeout=15,
                proxies={"http": None, "https": None},
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            return None, str(exc)
        finally:
            if resp is not None:
                try:
                    resp.close()
                except Exception:
                    pass

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

    def _aria2_rpc_cancel(self, gid: str):
        if not gid:
            return
        data, err = self._aria2_rpc_call("aria2.remove", [gid])
        if not data and err:
            self._aria2_rpc_call("aria2.forceRemove", [gid])
        self._aria2_rpc_remove_result(gid)

    def _subscription_thumbnail_cache_path(self, video_id: str, thumbnail_url: str) -> str:
        video_id = self._sanitize_path_segment(str(video_id or "").strip())
        thumbnail_url = str(thumbnail_url or "").strip()
        if not video_id or not thumbnail_url:
            return ""
        url_name = os.path.basename(urlparse(thumbnail_url).path)
        ext = os.path.splitext(url_name)[1].lower()
        if not re.match(r"^\.[a-z0-9]{1,8}$", ext):
            ext = ".jpg"
        fingerprint = hashlib.sha1(thumbnail_url.encode("utf-8")).hexdigest()[:12]
        img_dir = os.path.join(app_config.app_data_dir, "img")
        return os.path.join(img_dir, f"cover_{video_id}_{fingerprint}{ext}")

    def _subscription_avatar_cache_path(self, source: dict[str, Any], avatar_url: str) -> str:
        source_id = int(source.get("id", 0) or 0)
        source_key = self._sanitize_path_segment(str(source.get("source_key", "") or "author"))
        url_name = os.path.basename(urlparse(str(avatar_url or "")).path)
        ext = os.path.splitext(url_name)[1].lower()
        if not re.match(r"^\.[a-z0-9]{1,8}$", ext):
            ext = ".jpg"
        avatar_id = ""
        parts = [part for part in urlparse(str(avatar_url or "")).path.split("/") if part]
        if len(parts) >= 2:
            avatar_id = self._sanitize_path_segment(parts[-2])
        suffix = avatar_id or uuid.uuid4().hex
        img_dir = os.path.join(app_config.app_data_dir, "img")
        return os.path.join(img_dir, f"avatar_{source_id}_{source_key}_{suffix}{ext}")

    def _download_subscription_avatar(self, avatar_url: str, avatar_path: str) -> bool:
        if not avatar_url or not avatar_path:
            return False
        if os.path.isfile(avatar_path) and os.path.getsize(avatar_path) > 0:
            return True
        os.makedirs(os.path.dirname(avatar_path), exist_ok=True)
        temp_path = f"{avatar_path}.tmp"
        resp = None
        try:
            token = self._current_token()
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            resp = self.api.scraper.get(avatar_url, headers=headers, stream=True, timeout=30)
            if resp.status_code != 200:
                return False
            content_type = str(resp.headers.get("content-type", "") or "").lower()
            if content_type and not content_type.startswith("image/"):
                return False
            with open(temp_path, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk:
                        fh.write(chunk)
            if os.path.isfile(temp_path) and os.path.getsize(temp_path) > 0:
                os.replace(temp_path, avatar_path)
                return True
            return False
        except Exception:
            return False
        finally:
            if resp is not None:
                resp.close()
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

    def _download_thumbnail(self, task: DownloadTask, *, require_video_file: bool = True) -> bool:
        if not task.file_path:
            return False
        if require_video_file:
            if not os.path.exists(task.file_path):
                return False
            if os.path.getsize(task.file_path) <= 0:
                return False
        if not task.file_id or not task.file_url:
            signal_bus.log_message.emit(
                tr(
                    f"  [Thumbnail] \"{task.title}\" missing file_id/file_url, skipped",
                    f"  [封面] 《{task.title}》 缺少 file_id/file_url，跳过",
                    f"  [サムネイル] 「{task.title}」file_id/file_url 欠落のためスキップ",
                )
            )
            return False

        host = urlparse(task.file_url).netloc
        if not host:
            signal_bus.log_message.emit(
                tr(
                    f"  [Thumbnail] \"{task.title}\" invalid file_url, skipped",
                    f"  [封面] 《{task.title}》 无效 file_url，跳过",
                    f"  [サムネイル] 「{task.title}」無効な file_url のためスキップ",
                )
            )
            return False

        thumbnail_path = os.path.splitext(task.file_path)[0] + ".jpg"
        temp_path = f"{thumbnail_path}_temp"
        os.makedirs(os.path.dirname(thumbnail_path), exist_ok=True)
        if os.path.exists(thumbnail_path) and os.path.getsize(thumbnail_path) > 0:
            task.thumbnail_path = thumbnail_path
            return True

        index = max(0, int(task.thumbnail_index))
        thumb_url = f"https://{host}/image/original/{task.file_id}/thumbnail-{index:02d}.jpg"
        resp = None
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
                return False
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
                return True

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
        finally:
            if resp is not None:
                try:
                    resp.close()
                except Exception:
                    pass
        return False

    def _write_nfo(self, task: DownloadTask, *, require_video_file: bool = True) -> bool:
        if not task.file_path:
            return False
        if require_video_file:
            if not os.path.exists(task.file_path):
                return False
            if os.path.getsize(task.file_path) <= 0:
                return False

        nfo_path = os.path.splitext(task.file_path)[0] + ".nfo"
        tags = parse_nfo_tags(task.tags_json)
        nfo_text = build_nfo_text(task, tags)

        temp_path = f"{nfo_path}_temp"
        try:
            os.makedirs(os.path.dirname(nfo_path), exist_ok=True)
            ET.fromstring(nfo_text)
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
            return True
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
        return False

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
        with self._lock:
            live_task = self._tasks.get(task_id)
            if live_task:
                with self._existing_file_index_lock:
                    self._existing_file_index[live_task.video_id.lower()] = live_task.file_path
                self._mark_terminal_locked(live_task)
        signal_bus.task_status_changed.emit(task_id, TaskStatus.COMPLETED.value)
        self._prune_terminal_tasks()
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


def _dict_or_empty(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _iwara_image_url(image: dict[str, Any], *, variant: str = "thumbnail") -> str:
    image_id = str(image.get("id", "") or "").strip()
    name = str(image.get("name", "") or "").strip()
    variant = str(variant or "thumbnail").strip() or "thumbnail"
    if image_id and name:
        return f"https://i.iwara.tv/image/{quote(variant)}/{quote(image_id)}/{quote(name)}"
    path = str(image.get("path", "") or "").strip().strip("/")
    if path and name:
        encoded_path = "/".join(quote(part) for part in path.split("/") if part)
        return f"https://i.iwara.tv/image/{quote(variant)}/{encoded_path}/{quote(name)}"
    return ""


def _clip_stored_text(value: Any, limit: int = _MAX_STORED_TEXT_CHARS) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[:limit]


def _copy_compact_fields(data: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key in keys:
        value = data.get(key)
        if value is None or value == "":
            continue
        if isinstance(value, str):
            compact[key] = _clip_stored_text(value)
        elif isinstance(value, (int, float, bool)):
            compact[key] = value
    return compact


def _compact_video_raw_json(video_info: dict[str, Any]) -> str:
    """Keep only NFO-relevant API fields instead of the full video payload."""
    if not isinstance(video_info, dict):
        return "{}"

    compact = _copy_compact_fields(
        video_info,
        (
            "id",
            "title",
            "slug",
            "rating",
            "createdAt",
            "body",
            "description",
            "message",
        ),
    )

    user = _dict_or_empty(video_info.get("user"))
    if user:
        compact_user = _copy_compact_fields(
            user,
            ("id", "username", "name", "body", "description", "bio", "about"),
        )
        profile = _dict_or_empty(user.get("profile"))
        if profile:
            compact_profile = _copy_compact_fields(
                profile,
                ("body", "description", "bio", "about"),
            )
            if compact_profile:
                compact_user["profile"] = compact_profile
        if compact_user:
            compact["user"] = compact_user

    return json.dumps(compact, ensure_ascii=False, separators=(",", ":"))


def _subscription_item_from_video(video: dict[str, Any]) -> dict[str, Any]:
    video_id = str(video.get("id", "") or video.get("videoId", "") or "").strip()
    user = video.get("user")
    author = ""
    if isinstance(user, dict):
        author = str(user.get("username") or user.get("name") or "").strip()
    download_state, download_reason = _subscription_download_block_from_video_info(video)
    return {
        "video_id": video_id,
        "title": str(video.get("title", "") or video_id),
        "author": author,
        "published_at": str(video.get("createdAt", "") or video.get("updatedAt", "") or ""),
        "source_url": f"https://www.iwara.tv/video/{video_id}" if video_id else "",
        "thumbnail_url": _subscription_thumbnail_url(video),
        "download_state": download_state,
        "download_reason": download_reason,
        "download_state_known": bool(download_state or download_reason or video.get("fileUrl")),
    }


def _subscription_thumbnail_url(video: dict[str, Any]) -> str:
    custom_thumbnail = _dict_or_empty(video.get("customThumbnail"))
    if custom_thumbnail:
        custom_url = _iwara_image_url(custom_thumbnail, variant="original")
        if custom_url:
            return custom_url
    file_info = _dict_or_empty(video.get("file"))
    file_id = str(file_info.get("id", "") or "").strip()
    host = urlparse(str(video.get("fileUrl", "") or "")).netloc
    if not file_id or not host:
        return ""
    index = max(0, int(video.get("thumbnail", 0) or 0))
    return f"https://{host}/image/original/{quote(file_id)}/thumbnail-{index:02d}.jpg"


def _subscription_download_block_from_video_info(video_info: dict[str, Any]) -> tuple[str, str]:
    file_url = str(video_info.get("fileUrl", "") or "")
    if file_url:
        return "", ""
    embed = str(video_info.get("embedUrl", "") or "")
    embed_lower = embed.lower()
    if "youtube" in embed_lower or "youtu.be" in embed_lower:
        return _SUBSCRIPTION_UNAVAILABLE_STATE, tr(
            f"External YouTube embed; cannot be downloaded directly: {embed}",
            f"YouTube 外部嵌入视频，无法直接下载：{embed}",
            f"YouTube 外部埋め込みのため直接保存できません: {embed}",
        )
    message = str(video_info.get("message", "") or "")
    private = bool(video_info.get("private"))
    if message == "errors.privateVideo" or private:
        return _SUBSCRIPTION_UNAVAILABLE_STATE, tr(
            "Private video. The current account has no permission to download it.",
            "私有作品，当前账号没有权限下载。",
            "非公開動画です。現在のアカウントには保存権限がありません。",
        )
    return "", ""


def _subscription_download_block_from_error(error: str) -> tuple[str, str]:
    text = str(error or "").strip()
    lower = text.lower()
    if (
        "no permission" in lower
        or "403" in lower
        or "forbidden" in lower
        or "没有权限" in text
        or "不可见" in text
        or "私有" in text
        or "private" in lower
    ):
        return _SUBSCRIPTION_UNAVAILABLE_STATE, tr(
            "The current account has no permission to view or download this video.",
            "当前账号没有权限查看或下载该作品。",
            "現在のアカウントにはこの動画を表示または保存する権限がありません。",
        )
    return "", ""


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
