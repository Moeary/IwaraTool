"""Shared cooperative shutdown helpers for page-owned Qt worker threads."""
from __future__ import annotations

import time
from collections.abc import Iterable

from PySide6.QtCore import QThread


def stop_qthreads(
    workers: Iterable[QThread | None],
    *,
    timeout_ms: int = 30_000,
) -> bool:
    """Request interruption and wait up to one shared deadline for all workers."""
    unique: list[QThread] = []
    seen: set[int] = set()
    for worker in workers:
        if worker is None or id(worker) in seen:
            continue
        seen.add(id(worker))
        try:
            if worker.isRunning():
                worker.requestInterruption()
                unique.append(worker)
        except RuntimeError:
            continue

    deadline = time.monotonic() + max(0, int(timeout_ms)) / 1000
    for worker in unique:
        remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
        try:
            if worker.isRunning() and not worker.wait(remaining_ms):
                return False
        except RuntimeError:
            continue
    return True
