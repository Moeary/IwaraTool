"""Deliver ``signal_bus`` events to the Flet event loop.

Without Qt, ``signal_bus`` calls its slots right away on the emitting thread
(a download worker, the background service).  Flet controls must only be
touched from the event loop, so each subscription's slot only hands the
event over with ``loop.call_soon_threadsafe``:

* plain events are delivered one by one, in the order they were emitted;
* events subscribed with ``coalesce=`` (download progress) keep only the
  latest arguments per key and are delivered together every
  ``flush_interval`` seconds, so a busy download cannot flood the loop.

A handler may be a plain function or a coroutine function.  Pages keep their
subscriptions in a ``SubscriptionGroup`` and close it when they unmount, so
visiting a page again does not add receivers.

This module imports neither Flet nor Qt; tests drive it with a plain loop.
"""
from __future__ import annotations

import asyncio
import contextvars
import inspect
import threading
from collections.abc import Callable, Hashable
from typing import Any

from ...logging_setup import get_logger

logger = get_logger(__name__)

DEFAULT_FLUSH_INTERVAL = 0.15  # seconds between coalesced deliveries (docs/flet_ui_plan.md C6)

Handler = Callable[..., Any]


class Subscription:
    def __init__(self, bridge: "EventBridge", signal: Any, slot: Callable[..., None]):
        self._bridge = bridge
        self._signal = signal
        self._slot = slot
        self.closed = False

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self._signal.disconnect(self._slot)


class SubscriptionGroup:
    """The subscriptions of one page; ``close()`` ends them all."""

    def __init__(self, bridge: "EventBridge"):
        self._bridge = bridge
        self._items: list[Subscription] = []

    def on(self, signal_name: str, handler: Handler, *, coalesce: Callable[..., Hashable] | None = None) -> Subscription:
        subscription = self._bridge.subscribe(signal_name, handler, coalesce=coalesce)
        self._items.append(subscription)
        return subscription

    def close(self) -> None:
        items, self._items = self._items, []
        for subscription in items:
            subscription.close()

    def __len__(self) -> int:
        return len(self._items)


class EventBridge:
    """Moves events from any thread onto ``loop``; one per app window."""

    def __init__(self, loop: asyncio.AbstractEventLoop, *, bus: Any = None, flush_interval: float = DEFAULT_FLUSH_INTERVAL):
        if bus is None:
            from ...signal_bus import signal_bus as bus
        self._loop = loop
        self._bus = bus
        # Handlers change observable stores, and a store can only schedule a
        # re-render inside the Flet page context: capture it here (the bridge
        # is made in the page's callback) and deliver every event within it.
        self._context = contextvars.copy_context()
        self._flush_interval = flush_interval
        self._lock = threading.Lock()
        self._pending: dict[tuple[int, Hashable], tuple[Handler, tuple]] = {}  # coalesced, latest per key
        self._flush_scheduled = False
        self._closed = False

    # ── subscribing ──────────────────────────────────────────────────────────

    def group(self) -> SubscriptionGroup:
        return SubscriptionGroup(self)

    def subscribe(self, signal_name: str, handler: Handler, *, coalesce: Callable[..., Hashable] | None = None) -> Subscription:
        """Call ``handler(*args)`` on the loop for each ``signal_bus.<signal_name>`` emit.

        With ``coalesce(*args) -> key``, only the latest arguments per key are
        delivered, at most every ``flush_interval`` seconds.
        """

        signal = getattr(self._bus, signal_name)
        token = object()

        if coalesce is None:
            def slot(*args: Any) -> None:
                self._post(self._deliver, handler, args)
        else:
            def slot(*args: Any) -> None:
                key = (id(token), coalesce(*args))
                with self._lock:
                    if self._closed:
                        return
                    self._pending[key] = (handler, args)
                    schedule = not self._flush_scheduled
                    self._flush_scheduled = True
                if schedule:
                    self._post(self._schedule_flush)

        signal.connect(slot)
        return Subscription(self, signal, slot)

    def close(self) -> None:
        """Stop delivering; events still queued are dropped."""

        with self._lock:
            self._closed = True
            self._pending.clear()

    # ── delivery (on the loop) ───────────────────────────────────────────────

    def _post(self, callback: Callable[..., None], *args: Any) -> None:
        if self._closed:
            return
        try:
            self._loop.call_soon_threadsafe(callback, *args, context=self._context.copy())
        except RuntimeError:  # the loop is closed (the app is exiting)
            pass

    def _schedule_flush(self) -> None:
        self._loop.call_later(self._flush_interval, self._flush, context=self._context.copy())

    def _flush(self) -> None:
        with self._lock:
            pending, self._pending = self._pending, {}
            self._flush_scheduled = False
        for handler, args in pending.values():
            self._deliver(handler, args)

    def _deliver(self, handler: Handler, args: tuple) -> None:
        if self._closed:
            return
        try:
            result = handler(*args)
            if inspect.isawaitable(result):
                task = asyncio.ensure_future(result)
                task.add_done_callback(_log_failure)
        except Exception:
            logger.exception("Event handler %r failed", handler)


def _log_failure(task: asyncio.Future) -> None:
    if not task.cancelled() and task.exception() is not None:
        logger.error("Event handler task failed", exc_info=task.exception())
