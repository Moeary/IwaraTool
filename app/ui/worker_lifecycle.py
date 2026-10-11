"""Shared cooperative shutdown helpers for page-owned Qt worker threads."""
from __future__ import annotations

import time
from collections.abc import Callable, Iterable

import shiboken6
from PySide6.QtCore import QObject, QThread, QTimer, Signal


_RUNNING: set[QThread] = set()


class ManagedThread(QThread):
    """A page worker that cannot be destroyed while it is running.

    Destroying a running QThread aborts the whole process.  That can happen
    without anyone deleting the thread on purpose: its Python owner (a page)
    is garbage-collected, or a Qt parent widget is destroyed.  ``start()``
    therefore keeps a strong reference, and detaches the thread from any Qt
    parent, until the thread's own ``finished`` has been delivered.
    """

    def start(self, *args, **kwargs):
        if self.parent() is not None:
            self.setParent(None)
        if self not in _RUNNING:
            _RUNNING.add(self)
            self.finished.connect(self._release_keepalive)
        super().start(*args, **kwargs)

    def _release_keepalive(self):
        # Let go on the next event-loop turn, not while ``finished`` is being delivered.
        QTimer.singleShot(0, lambda thread=self: _RUNNING.discard(thread))


def on_finished(worker: QThread, owner: QObject, callback: Callable[[QThread], None]) -> None:
    """Call ``callback(worker)`` when ``worker`` finishes, unless ``owner`` is gone by then.

    A worker can outlive the page that started it (see ``ManagedThread``);
    its clean-up must not then touch the page's destroyed widgets.
    """

    def finished():
        if shiboken6.isValid(owner):
            callback(worker)

    worker.finished.connect(finished)


def running_threads() -> int:
    """How many managed threads are still being kept alive (diagnostics, tests)."""
    return len(_RUNNING)


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


class ShutdownPoller(QObject):
    """Stops page workers without blocking the GUI thread.

    Each step is a page's ``shutdown(timeout_ms)``; called with ``0`` it asks
    the page's workers to stop and reports, without waiting, whether they have.
    The first pass runs every step at once, so all pages are cancelled
    together; later passes run on a timer under one shared deadline, and the
    event loop keeps delivering ``finished`` signals in between.
    """

    done = Signal(bool)  # True: everything stopped; False: the deadline passed

    def __init__(
        self,
        steps: Iterable[Callable[..., bool]],
        *,
        timeout_ms: int = 30_000,
        interval_ms: int = 50,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._steps = list(steps)
        self._deadline = time.monotonic() + max(0, int(timeout_ms)) / 1000
        self._finished = False
        self._timer = QTimer(self)
        self._timer.setInterval(max(1, int(interval_ms)))
        self._timer.timeout.connect(self._tick)

    def poll(self) -> bool:
        """One pass over every step; True once all of them report stopped."""
        results = []
        for step in self._steps:
            try:
                results.append(bool(step(timeout_ms=0)))
            except RuntimeError:  # the page is already gone
                results.append(True)
        return all(results)

    def start(self) -> bool:
        """Run the first pass now; True if nothing had to be waited for."""
        if self.poll():
            self._finish(True, emit=False)
            return True
        self._timer.start()
        return False

    def _tick(self):
        if self._finished:
            return
        if self.poll():
            self._finish(True)
        elif time.monotonic() >= self._deadline:
            self._finish(False)

    def _finish(self, ok: bool, *, emit: bool = True):
        self._finished = True
        self._timer.stop()
        if emit:
            self.done.emit(ok)
