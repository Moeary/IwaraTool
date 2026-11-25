"""Scan, preview or repair a folder off the GUI thread."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget

from ...core.repair import runs
from ..common.worker_lifecycle import ManagedThread
from .page_dependencies import download_manager


class RepairWorker(ManagedThread):
    """Run one repair phase without blocking the Qt event loop."""

    # Finished rows arrive in batches: one queued signal (and one table
    # update) per batch keeps the event loop responsive for thousands of files.
    items_ready = Signal(list)
    progress = Signal(int, int)
    activity = Signal(int, int, int)
    result_ready = Signal(object)
    error = Signal(str)

    BATCH_INTERVAL = 0.2

    def __init__(
        self,
        mode: str,
        *,
        folder: str = "",
        filename_template: str = "",
        output_root: str = "",
        move_to_output: bool = False,
        items: list[dict[str, Any]] | None = None,
        options: dict[str, Any] | None = None,
        path: str = "",
        video_id_text: str = "",
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.mode = mode
        self.path = path
        self.video_id_text = video_id_text
        # A stopped lookup cannot cancel its request, but drops the result.
        self.discarded = False
        self.folder = folder
        self.filename_template = filename_template
        self.output_root = output_root
        self.move_to_output = bool(move_to_output)
        self.items = list(items or [])
        self.options = dict(options or {})

    def run(self):
        stop = self.isInterruptionRequested
        try:
            if self.mode == "scan":
                result = runs.scan(
                    download_manager,
                    self.folder,
                    filename_template=self.filename_template,
                    output_root=self.output_root,
                    move_to_output=self.move_to_output,
                    options=self.options,
                    on_batch=self.items_ready.emit,
                    on_progress=self.progress.emit,
                    on_activity=self.activity.emit,
                    should_stop=stop,
                    interval=self.BATCH_INTERVAL,
                )
            elif self.mode == "resolve":
                result = runs.resolve(download_manager, self.items, self.path, self.video_id_text, options=self.options)
                if self.isInterruptionRequested():
                    self.discarded = True
                    return
            else:
                result = runs.repair(
                    download_manager,
                    self.items,
                    options=self.options,
                    on_batch=self.items_ready.emit,
                    on_progress=self.progress.emit,
                    should_stop=stop,
                    interval=self.BATCH_INTERVAL,
                )
            self.result_ready.emit(result)
        except Exception as exc:
            self.error.emit(str(exc))


class RepairPreviewWorker(ManagedThread):
    """Plan target paths and check them for conflicts off the GUI thread.

    The conflict check stats several paths per file; for a large folder that
    took seconds and used to run on every option change in the GUI thread.
    """

    result_ready = Signal(int, object)
    error = Signal(str)

    def __init__(
        self,
        generation: int,
        items: list[dict[str, Any]],
        options: dict[str, Any],
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.generation = generation
        self.items = [dict(item) for item in items]
        self.options = dict(options)

    def run(self):
        try:
            result = runs.preview(download_manager, self.items, self.options)
        except Exception as exc:
            self.error.emit(str(exc))
            return
        if not self.isInterruptionRequested():
            self.result_ready.emit(self.generation, result)
