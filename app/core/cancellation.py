"""Cancellation that works on any thread, not only on a QThread.

A ``CancelToken`` is set when its work is no longer wanted (the page closed,
a newer search replaced it, the app is exiting).  ``cancel_scope(token)``
makes it the current token of the running context, so code deep in the core
(``net_policy`` requests, back-off waits, the Oreno3D client) can give up
without the token being passed down every call.

The current token lives in a ``contextvars.ContextVar``: ``asyncio.to_thread``
copies the context into its worker thread, so::

    token = CancelToken()

    def work():
        with cancel_scope(token):
            return fetch_page()

    result = await asyncio.to_thread(work)   # token.cancel() stops it early

The PySide6 interface keeps using ``QThread.requestInterruption``;
``net_policy.cancellation_requested`` checks both.
"""
from __future__ import annotations

import contextvars
import threading
from collections.abc import Iterator
from contextlib import contextmanager


class CancelToken:
    """A one-way switch: once cancelled it stays cancelled."""

    __slots__ = ("_event", "reason")

    def __init__(self) -> None:
        self._event = threading.Event()
        self.reason = ""

    def cancel(self, reason: str = "") -> None:
        if not self._event.is_set():
            self.reason = reason
            self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        """Block up to ``timeout`` seconds; True as soon as the token is cancelled."""
        return self._event.wait(timeout)

    def __repr__(self) -> str:
        state = f"cancelled: {self.reason}" if self.cancelled else "active"
        return f"<CancelToken {state}>"


_current: contextvars.ContextVar[CancelToken | None] = contextvars.ContextVar("cancel_token", default=None)


def current_token() -> CancelToken | None:
    return _current.get()


def token_cancelled() -> bool:
    """Whether the current context's token (if any) was cancelled."""
    token = _current.get()
    return token is not None and token.cancelled


@contextmanager
def cancel_scope(token: CancelToken | None) -> Iterator[CancelToken | None]:
    """Make ``token`` the current token for the block (no-op for ``None``)."""
    if token is None:
        yield _current.get()
        return
    reset = _current.set(token)
    try:
        yield token
    finally:
        _current.reset(reset)
