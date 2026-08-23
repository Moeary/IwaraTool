"""Atomic persistence for recoverable download-queue state."""
from __future__ import annotations

import json
import os
import threading
from dataclasses import fields
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from ..config import app_config
from .models import DownloadTask, TaskStatus


TASK_QUEUE_SCHEMA_VERSION = 1

_PERSISTED_STATUSES = frozenset(
    {
        TaskStatus.QUEUED_META,
        TaskStatus.RESOLVING,
        TaskStatus.QUEUED_DOWNLOAD,
        TaskStatus.DOWNLOADING,
        TaskStatus.CANCELLING,
        TaskStatus.CANCELLED,
        TaskStatus.FAILED,
    }
)
_REQUEUE_ON_LOAD = frozenset(
    {
        TaskStatus.RESOLVING,
        TaskStatus.QUEUED_DOWNLOAD,
        TaskStatus.DOWNLOADING,
        TaskStatus.CANCELLING,
    }
)
_TASK_FIELDS = tuple(field.name for field in fields(DownloadTask))
_INT_FIELDS = {
    "total_bytes",
    "downloaded_bytes",
    "likes",
    "views",
    "duration",
    "comments",
    "thumbnail_index",
}
_SIGNED_INT_FIELDS = {"priority"}
_BOOL_FIELDS = {
    "cancel_requested",
    "delete_temp_on_cancel",
    "remove_after_cancel",
}


class TaskQueueStore:
    """Read and atomically replace the portable task-queue snapshot."""

    def __init__(self, path: str | None = None):
        self.path = path or os.path.join(app_config.app_data_dir, "tasks.json")
        self.last_error = ""

    @staticmethod
    def create_snapshot(tasks: Iterable[DownloadTask]) -> dict[str, Any]:
        serialized = [
            TaskQueueStore._task_to_payload(task)
            for task in tasks
            if task.status in _PERSISTED_STATUSES
        ]
        return {
            "version": TASK_QUEUE_SCHEMA_VERSION,
            "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "tasks": serialized,
        }

    def save(self, tasks: Iterable[DownloadTask]):
        self.save_snapshot(self.create_snapshot(tasks))

    def save_snapshot(self, snapshot: dict[str, Any]):
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        temp_path = f"{self.path}.{os.getpid()}.{threading.get_ident()}.tmp"
        try:
            with open(temp_path, "w", encoding="utf-8") as stream:
                json.dump(snapshot, stream, ensure_ascii=False, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, self.path)
            self.last_error = ""
        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

    def load(self) -> list[DownloadTask]:
        self.last_error = ""
        if not os.path.exists(self.path):
            return []
        try:
            with open(self.path, "r", encoding="utf-8") as stream:
                payload = json.load(stream)
            task_payloads = payload.get("tasks") if isinstance(payload, dict) else None
            if not isinstance(task_payloads, list):
                raise ValueError("task queue snapshot has no task list")

            tasks: list[DownloadTask] = []
            seen_task_ids: set[str] = set()
            seen_video_ids: set[str] = set()
            for item in task_payloads:
                task = self._task_from_payload(item)
                if task is None:
                    continue
                video_key = task.video_id.casefold()
                if task.task_id in seen_task_ids or video_key in seen_video_ids:
                    continue
                seen_task_ids.add(task.task_id)
                seen_video_ids.add(video_key)
                tasks.append(task)
            return tasks
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            self.last_error = str(exc)
            return []

    @staticmethod
    def _task_to_payload(task: DownloadTask) -> dict[str, Any]:
        payload = {name: getattr(task, name) for name in _TASK_FIELDS}
        payload["status"] = task.status.value
        return payload

    @staticmethod
    def _task_from_payload(payload: Any) -> DownloadTask | None:
        if not isinstance(payload, dict):
            return None
        task_id = str(payload.get("task_id", "") or "").strip()
        video_id = str(payload.get("video_id", "") or "").strip()
        if not task_id or not video_id:
            return None
        try:
            status = TaskStatus(str(payload.get("status", TaskStatus.QUEUED_META.value)))
        except ValueError:
            return None
        if status not in _PERSISTED_STATUSES:
            return None

        values: dict[str, Any] = {
            "task_id": task_id,
            "video_id": video_id,
            "url": str(payload.get("url", "") or ""),
            "status": status,
        }
        for name in _TASK_FIELDS:
            if name in values or name == "status" or name not in payload:
                continue
            value = payload[name]
            if name in _INT_FIELDS:
                try:
                    values[name] = max(0, int(value or 0))
                except (TypeError, ValueError):
                    values[name] = 0
            elif name in _SIGNED_INT_FIELDS:
                try:
                    values[name] = max(-100, min(100, int(value or 0)))
                except (TypeError, ValueError):
                    values[name] = 0
            elif name in _BOOL_FIELDS:
                values[name] = bool(value)
            else:
                values[name] = str(value or "")

        task = DownloadTask(**values)
        if status in _REQUEUE_ON_LOAD:
            task.status = TaskStatus.QUEUED_META
            task.download_url = ""
            task.speed_str = ""
            task.error_msg = ""
        task.cancel_requested = False
        task.delete_temp_on_cancel = False
        task.remove_after_cancel = False
        task.aria2_gid = ""
        return task


class TaskPersistenceCoordinator:
    """Debounce queue writes and provide a synchronous shutdown flush."""

    def __init__(
        self,
        store: TaskQueueStore,
        snapshot_provider: Callable[[], dict[str, Any]],
        *,
        delay_seconds: float = 0.25,
    ):
        self._store = store
        self._snapshot_provider = snapshot_provider
        self._delay_seconds = max(0.01, float(delay_seconds))
        self._state_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._closed = False
        self.last_error = ""

    def schedule(self):
        with self._state_lock:
            if self._closed:
                return
            if self._timer is not None:
                self._timer.cancel()
            timer = threading.Timer(self._delay_seconds, self._run_scheduled_save)
            timer.daemon = True
            self._timer = timer
            timer.start()

    def flush(self):
        with self._state_lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        self._write_snapshot()

    def close(self):
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        self._write_snapshot()

    def _run_scheduled_save(self):
        with self._state_lock:
            if self._closed:
                return
            self._timer = None
        self._write_snapshot()

    def _write_snapshot(self):
        try:
            with self._write_lock:
                self._store.save_snapshot(self._snapshot_provider())
            self.last_error = ""
        except (OSError, TypeError, ValueError) as exc:
            self.last_error = str(exc)
