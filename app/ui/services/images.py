"""Covers and avatars through the core's image cache.

The Flet client could load remote URLs itself, but then the app's session,
proxy and cache would be bypassed.  ``ImageService`` runs the core's
``cache_search_image`` on a bounded pool (desktop 6–16 workers from Settings,
each with its own API client) and remembers results.  Only mounted controls
ask, and lists build on demand, so what is on screen loads first.
"""
from __future__ import annotations

import asyncio
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import flet as ft

from ...core.search.limits import configured_cover_concurrency
from ...logging_setup import get_logger

logger = get_logger(__name__)

# Subscription works: list endpoints often return a stub without a cover URL;
# the core then looks the video up (and remembers the URL), as the Classic list does.
SUBSCRIPTION = "subscription"
RESOLVES_WITHOUT_URL = frozenset({SUBSCRIPTION})


class ImageService:
    def __init__(self, manager: Any, *, workers: int | None = None):
        self.manager = manager
        self._executor = ThreadPoolExecutor(max_workers=workers or configured_cover_concurrency(), thread_name_prefix="flet-image")
        self._local = threading.local()
        self._clients: list[Any] = []
        self._clients_lock = threading.Lock()
        self._paths: dict[tuple[str, str], str] = {}
        self._pending: dict[tuple[str, str], asyncio.Future] = {}

    def known(self, kind: str, key: str) -> str:
        return self._paths.get((kind, key), "")

    def _fetch(self, kind: str, key: str, url: str) -> str:
        client = getattr(self._local, "client", None)
        create = getattr(self.manager, "create_worker_api_client", None)
        if client is None and callable(create):
            client = create()
            self._local.client = client
            with self._clients_lock:
                self._clients.append(client)
        try:
            if kind == SUBSCRIPTION:
                return self.manager.cache_subscription_thumbnail(key, url, api_client=client) or ""
            return self.manager.cache_search_image(kind, key, url, api_client=client) or ""
        except Exception:
            logger.debug("Image %s/%s could not be cached", kind, key, exc_info=True)
            return ""

    async def get(self, kind: str, key: str, url: str) -> str:
        """Local path of the image (``""`` when it cannot be had)."""

        ident = (kind, key)
        if ident in self._paths:
            return self._paths[ident]
        if not url and kind not in RESOLVES_WITHOUT_URL:
            return ""
        pending = self._pending.get(ident)
        if pending is None:
            loop = asyncio.get_running_loop()
            pending = loop.run_in_executor(self._executor, self._fetch, kind, key, url)
            self._pending[ident] = pending
        try:
            path = await asyncio.shield(pending)
        finally:
            self._pending.pop(ident, None)
        if path:
            self._paths[ident] = path
        return path

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
        close = getattr(self.manager, "close_worker_api_client", None)
        if callable(close):
            for client in self._clients:
                try:
                    close(client)
                except Exception:
                    pass


def use_cached_image(service: ImageService, kind: str, key: str, url: str, local: str = "") -> str:
    """Hook: the image's local path once cached (``local`` first when that file exists)."""

    initial = local if local and os.path.isfile(local) else service.known(kind, key)
    path, set_path = ft.use_state(initial)

    async def load() -> None:
        if path or not key or not url and kind not in RESOLVES_WITHOUT_URL:
            return
        found = await service.get(kind, key, url)
        if found:
            set_path(found)

    ft.use_effect(load, [kind, key, url])
    return path
