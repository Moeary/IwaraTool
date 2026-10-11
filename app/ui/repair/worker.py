"""Scan or repair a folder off the GUI thread."""

from __future__ import annotations

import time
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget

from ..worker_lifecycle import ManagedThread
from .page_dependencies import download_manager


class RepairWorker(ManagedThread):
    """Run one repair phase without blocking the Qt event loop."""

    item_ready = Signal(dict)
    progress = Signal(int, int)
    activity = Signal(int, int, int)
    result_ready = Signal(object)
    error = Signal(str)

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
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.mode = mode
        self.folder = folder
        self.filename_template = filename_template
        self.output_root = output_root
        self.move_to_output = bool(move_to_output)
        self.items = list(items or [])
        self.options = dict(options or {})
        self._last_progress_emit = 0.0
        self._last_activity_emit = 0.0

    def run(self):
        def callback(item: dict[str, Any], index: int, total: int) -> bool:
            if self.isInterruptionRequested():
                return False
            # A scan can contain hundreds of rows. Keep its result off the
            # table until all worker futures finish; repainting the table for
            # every resolved file makes the Qt event loop increasingly costly.
            if self.mode != "scan":
                self.item_ready.emit(dict(item))
            now = time.monotonic()
            if (
                self.mode != "scan"
                or index == total
                or now - self._last_progress_emit >= 0.15
            ):
                self._last_progress_emit = now
                self.progress.emit(index, total)
            return True

        def activity_callback(completed: int, total: int, active: int):
            if self.isInterruptionRequested():
                return
            now = time.monotonic()
            if completed == total or now - self._last_activity_emit >= 0.2:
                self._last_activity_emit = now
                self.activity.emit(completed, total, active)

        try:
            if self.mode == "scan":
                result = download_manager.scan_repair_folder(
                    self.folder,
                    filename_template=self.filename_template,
                    output_root=self.output_root,
                    move_to_output=self.move_to_output,
                    progress_callback=callback,
                    activity_callback=activity_callback,
                )
            else:
                result = download_manager.repair_folder_files(
                    self.items,
                    options=self.options,
                    progress_callback=callback,
                )
            self.result_ready.emit(result)
        except Exception as exc:
            self.error.emit(str(exc))
