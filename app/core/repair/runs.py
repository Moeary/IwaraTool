"""Scan, look up, preview and repair runs for the interfaces (off the UI thread).

The loops that used to live in the PySide6 worker: finished rows are handed
over in batches (one callback per ``BATCH_INTERVAL``) so the interface
redraws once per batch even for thousands of files.  ``should_stop`` follows
the current ``CancelToken`` (or QThread interruption) unless given;
``manager`` is the download manager.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from ..net_policy import cancellation_requested

BATCH_INTERVAL = 0.2

Batch = Callable[[list[dict[str, Any]]], None]
Progress = Callable[[int, int], None]
Activity = Callable[[int, int, int], None]


class _Batcher:
    def __init__(self, on_batch: Batch, on_progress: Progress, on_activity: Activity,
                 should_stop: Callable[[], bool], interval: float):
        self.on_batch, self.on_progress, self.on_activity = on_batch, on_progress, on_activity
        self.should_stop = should_stop
        self.interval = interval
        self.pending: list[dict[str, Any]] = []
        self.last_batch = 0.0
        self.last_activity = 0.0

    def item(self, item: dict[str, Any], index: int, total: int) -> bool:
        if self.should_stop():
            return False
        self.pending.append(dict(item))
        now = time.monotonic()
        if index == total or now - self.last_batch >= self.interval:
            self.last_batch = now
            self.flush()
            self.on_progress(index, total)
        return True

    def activity(self, completed: int, total: int, active: int) -> None:
        if self.should_stop():
            return
        now = time.monotonic()
        if completed == total or now - self.last_activity >= self.interval:
            self.last_activity = now
            self.on_activity(completed, total, active)

    def flush(self) -> None:
        if self.pending:
            batch, self.pending = self.pending, []
            self.on_batch(batch)


def _noop(*_args) -> None:
    return None


def scan(
    manager: Any,
    folder: str,
    *,
    filename_template: str,
    output_root: str = "",
    move_to_output: bool = False,
    options: dict[str, Any] | None = None,
    on_batch: Batch = _noop,
    on_progress: Progress = _noop,
    on_activity: Activity = _noop,
    should_stop: Callable[[], bool] = cancellation_requested,
    interval: float = BATCH_INTERVAL,
) -> list[dict[str, Any]]:
    """Identify every video under ``folder``; the planned items (targets and conflicts included)."""
    batcher = _Batcher(on_batch, on_progress, on_activity, should_stop, interval)
    try:
        return manager.scan_repair_folder(
            folder,
            filename_template=filename_template,
            output_root=output_root,
            move_to_output=move_to_output,
            preview_options=dict(options or {}),
            progress_callback=batcher.item,
            activity_callback=batcher.activity,
        )
    finally:
        batcher.flush()


def repair(
    manager: Any,
    items: list[dict[str, Any]],
    *,
    options: dict[str, Any] | None = None,
    on_batch: Batch = _noop,
    on_progress: Progress = _noop,
    should_stop: Callable[[], bool] = cancellation_requested,
    interval: float = BATCH_INTERVAL,
) -> dict[str, Any]:
    """Rename / move, fetch covers and NFO, record history; the summary with the final items."""
    batcher = _Batcher(on_batch, on_progress, _noop, should_stop, interval)
    try:
        return manager.repair_folder_files(list(items), options=dict(options or {}), progress_callback=batcher.item)
    finally:
        batcher.flush()


def resolve(manager: Any, items: list[dict[str, Any]], path: str, video_id_text: str, *,
            options: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Look one file up again by a typed Iwara ID or link; all items, re-planned."""
    return manager.resolve_repair_item(list(items), path, video_id_text, options=dict(options or {}))


def preview(manager: Any, items: list[dict[str, Any]], options: dict[str, Any]) -> list[dict[str, Any]]:
    """Plan target paths and check them for conflicts (stats several paths per file)."""
    return manager.preview_repair_files([dict(item) for item in items], options=dict(options))
