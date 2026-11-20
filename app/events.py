"""Pure-Python stand-in for Qt signals, used when PySide6 is not available.

It mirrors the part of the Qt API the app uses: a class attribute
``name = Signal(str, int)`` gives each instance a signal with ``connect``,
``disconnect`` and ``emit``. Unlike a Qt signal, ``emit`` calls the slots
right away on the emitting thread. A front end that has a UI thread hands
the call over to it itself, as an Android bridge posting to the main looper
does. An exception in one slot is logged and does not stop the others.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("iwaratool.events")


class BoundSignal:
    """One instance's signal: the connected slots of ``Signal`` ``name``."""

    def __init__(self, name: str):
        self._name = name
        self._slots: list[Callable[..., Any]] = []
        self._lock = threading.Lock()

    def connect(self, slot: Callable[..., Any]) -> Callable[..., Any]:
        if not callable(slot):
            raise TypeError(f"{self._name}.connect() needs a callable, got {slot!r}")
        with self._lock:
            self._slots.append(slot)
        return slot

    def disconnect(self, slot: Callable[..., Any] | None = None) -> bool:
        """Remove ``slot`` (its first connection), or every slot when None."""

        with self._lock:
            if slot is None:
                removed = bool(self._slots)
                self._slots.clear()
                return removed
            for index, connected in enumerate(self._slots):
                if connected == slot:
                    del self._slots[index]
                    return True
        return False

    def emit(self, *args: Any) -> None:
        with self._lock:
            slots = list(self._slots)
        for slot in slots:
            try:
                slot(*args)
            except Exception:
                logger.exception("Slot %r of signal %s failed", slot, self._name)

    def receivers(self) -> int:
        with self._lock:
            return len(self._slots)


class Signal:
    """Class attribute declaring a signal; ``types`` are documentation only."""

    def __init__(self, *types: Any):
        self.types = types
        self._name = ""

    def __set_name__(self, owner: type, name: str) -> None:
        self._name = name

    def __get__(self, obj: Any, objtype: type | None = None):
        if obj is None:
            return self
        bound = obj.__dict__.get(self._name)
        if bound is None:
            bound = obj.__dict__.setdefault(self._name, BoundSignal(self._name))
        return bound


class SignalHost:
    """Base class for objects declaring ``Signal`` attributes (QObject's place)."""
