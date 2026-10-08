"""Shared request policy for Iwara API sessions.

Adds three things the bare cloudscraper session lacks:

* a minimum interval between requests to the same host (cheap rate limiting),
* exponential back-off retries for transient failures, and
* ``Retry-After`` handling for HTTP 429/503.

Streaming downloads are deliberately not routed through here.
"""
from __future__ import annotations

import threading
import time
from email.utils import parsedate_to_datetime
from typing import Any, Callable
from urllib.parse import urlparse

from ..logging_setup import get_logger

logger = get_logger(__name__)

RETRY_STATUSES = frozenset({429, 502, 503, 504})
MAX_RETRY_AFTER_SECONDS = 30.0
BASE_BACKOFF_SECONDS = 1.0


def parse_retry_after(value: Any, now: float | None = None) -> float | None:
    """Seconds to wait from a ``Retry-After`` header (delta or HTTP date)."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    current = time.time() if now is None else now
    return max(0.0, when.timestamp() - current)


def backoff_delay(attempt: int, retry_after: float | None = None) -> float:
    """Delay before retry number ``attempt`` (1-based), capped."""
    if retry_after is not None:
        return min(retry_after, MAX_RETRY_AFTER_SECONDS)
    return min(BASE_BACKOFF_SECONDS * (2 ** (attempt - 1)), MAX_RETRY_AFTER_SECONDS)


class HostThrottle:
    """Enforces a minimum spacing between requests per host (thread-safe)."""

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._next_slot: dict[str, float] = {}

    def wait(self, host: str, min_interval: float) -> float:
        """Block until ``host`` may be hit again; returns the time slept."""
        if min_interval <= 0 or not host:
            return 0.0
        with self._lock:
            now = self._clock()
            slot = max(now, self._next_slot.get(host, 0.0))
            self._next_slot[host] = slot + min_interval
        delay = slot - now
        if delay > 0:
            self._sleep(delay)
        return max(delay, 0.0)


_throttle = HostThrottle()


def install(
    session: Any,
    *,
    min_interval: Callable[[], float],
    max_retries: Callable[[], int],
    sleep: Callable[[float], None] = time.sleep,
    throttle: HostThrottle | None = None,
) -> Any:
    """Wrap ``session.request`` (used by get/post/...) with the policy.

    ``min_interval`` (seconds) and ``max_retries`` are callables so settings
    changes apply without rebuilding the session.
    """
    original = getattr(session, "request", None)
    if not callable(original):
        return session
    gate = throttle or _throttle

    def request(method, url, *args, **kwargs):
        host = urlparse(str(url)).netloc.lower()
        retries = max(0, int(max_retries()))
        attempt = 0
        while True:
            gate.wait(host, float(min_interval()))
            try:
                response = original(method, url, *args, **kwargs)
            except Exception as exc:
                if attempt >= retries or not _is_transient_error(exc):
                    raise
                attempt += 1
                delay = backoff_delay(attempt)
                logger.warning(
                    "%s %s failed (%s); retry %d/%d in %.1fs",
                    method, host, exc.__class__.__name__, attempt, retries, delay,
                )
                sleep(delay)
                continue

            status = getattr(response, "status_code", 0)
            if status not in RETRY_STATUSES or attempt >= retries:
                return response
            attempt += 1
            retry_after = parse_retry_after(
                getattr(response, "headers", {}).get("Retry-After")
            )
            delay = backoff_delay(attempt, retry_after)
            logger.warning(
                "%s %s -> HTTP %s; retry %d/%d in %.1fs",
                method, host, status, attempt, retries, delay,
            )
            try:
                response.close()
            except Exception:
                pass
            sleep(delay)

    session.request = request
    return session


def _is_transient_error(exc: Exception) -> bool:
    try:
        import requests

        return isinstance(
            exc,
            (requests.exceptions.ConnectionError, requests.exceptions.Timeout),
        ) and not isinstance(exc, requests.exceptions.SSLError)
    except Exception:  # pragma: no cover
        return False
