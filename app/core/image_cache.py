"""Small, atomic image cache used by search result cards."""

from __future__ import annotations

import hashlib
import os
import re
import threading
from urllib.parse import urlparse

from ..logging_setup import get_logger

logger = get_logger(__name__)

# The first failures are logged as warnings with their reason (a phone whose
# network blocks the image host otherwise only shows placeholders); the rest at
# debug level so a bad connection cannot flood the log.
WARN_FAILURES = 10
_failures = 0
_failures_lock = threading.Lock()


def _log_failure(image_url: str, reason: str, exc_info: bool = False) -> None:
    global _failures
    with _failures_lock:
        _failures += 1
        warn = _failures <= WARN_FAILURES
    host = urlparse(image_url).netloc
    if warn:
        logger.warning("Image from %s could not be cached: %s", host, reason, exc_info=exc_info)
    else:
        logger.debug("Image fetch failed: %s (%s)", image_url, reason)


class SearchImageCache:
    """Cache remote search images under ``<image cache>/search`` (``data/img/search``).

    Files are written to a temporary sibling and atomically renamed.  This is
    important for the UI: a card never observes a partially downloaded image.
    """

    FOLDER = "search"

    def __init__(self, root: str | None = None):
        # Without ``root`` the folder follows ``app_config.image_cache_dir``,
        # which a phone moves to shared storage after start-up.
        self._root = os.path.abspath(root) if root else ""
        self._lock = threading.RLock()

    @property
    def root(self) -> str:
        if self._root:
            return self._root
        # Import lazily so this small utility stays easy to use in isolated
        # tests and does not make the config module import this package.
        from ..config import app_config

        return os.path.abspath(os.path.join(app_config.image_cache_dir, self.FOLDER))

    @staticmethod
    def _safe_segment(value: str, fallback: str = "item") -> str:
        segment = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip())
        return segment[:96] or fallback

    @staticmethod
    def _extension(url: str) -> str:
        name = os.path.basename(urlparse(str(url or "")).path)
        ext = os.path.splitext(name)[1].lower()
        return ext if re.fullmatch(r"\.[a-z0-9]{1,8}", ext or "") else ".jpg"

    def path_for(self, kind: str, item_key: str, image_url: str) -> str:
        kind_part = self._safe_segment(kind, "image")
        key_part = self._safe_segment(item_key)
        fingerprint = hashlib.sha1(str(image_url or "").encode("utf-8")).hexdigest()[:12]
        return os.path.join(
            self.root,
            f"{kind_part}_{key_part}_{fingerprint}{self._extension(image_url)}",
        )

    @staticmethod
    def _looks_like_image(data: bytes) -> bool:
        """Recognize common image signatures for servers with generic MIME types."""

        return data.startswith(
            (
                b"\xff\xd8\xff",  # JPEG
                b"\x89PNG\r\n\x1a\n",  # PNG
                b"GIF87a",
                b"GIF89a",
                b"BM",  # BMP
                b"RIFF",  # WebP; the full marker is checked below
            )
        ) and (not data.startswith(b"RIFF") or data[8:12] == b"WEBP")

    def get_or_fetch(
        self,
        kind: str,
        item_key: str,
        image_url: str,
        *,
        session,
        headers: dict[str, str] | None = None,
        force: bool = False,
    ) -> str:
        """Return a cached path, downloading the image when needed.

        ``force`` is used by an explicit refresh action.  It deliberately
        keeps the same deterministic path and atomically replaces the old
        file, so callers do not accumulate duplicate cache files.

        Network and image validation failures intentionally return an empty
        string.  Search cards can keep their placeholder without failing the
        entire result page.
        """
        image_url = str(image_url or "").strip()
        if not image_url or not image_url.startswith(("http://", "https://")):
            return ""
        path = self.path_for(kind, item_key, image_url)
        # Only the local existence check is serialized.  Holding the lock while
        # waiting for a remote image made every thumbnail worker effectively
        # single-threaded, even when callers used a pool.
        with self._lock:
            try:
                if not force and os.path.isfile(path) and os.path.getsize(path) > 0:
                    return path
            except OSError:
                return ""

        os.makedirs(self.root, exist_ok=True)
        temp_path = f"{path}.{threading.get_ident()}.tmp"
        response = None
        try:
            response = session.get(
                image_url,
                headers=headers or {},
                stream=True,
                timeout=30,
            )
            stored = self._store_response(response, temp_path, path)
            if not stored:
                content_type = getattr(response, "headers", {}).get("content-type", "")
                _log_failure(image_url, f"HTTP {getattr(response, 'status_code', '?')} {content_type}".strip())
            return stored
        except Exception as exc:
            _log_failure(image_url, f"{type(exc).__name__}: {exc}")
            return ""
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except OSError:
                pass

    def _store_response(self, response, temp_path: str, path: str) -> str:
        """Write an image response to ``path`` via ``temp_path``; "" unless it is a valid image."""

        if int(getattr(response, "status_code", 0) or 0) != 200:
            return ""
        content_type = str(
            getattr(response, "headers", {}).get("content-type", "") or ""
        ).lower()
        if content_type and not (
            content_type.startswith("image/")
            or content_type in {"application/octet-stream", "binary/octet-stream"}
        ):
            return ""
        signature = bytearray()
        with open(temp_path, "wb") as stream:
            for chunk in response.iter_content(chunk_size=65536):
                if chunk:
                    if len(signature) < 16:
                        signature.extend(chunk[: 16 - len(signature)])
                    stream.write(chunk)
        if not content_type.startswith("image/") and not self._looks_like_image(bytes(signature)):
            return ""
        if os.path.isfile(temp_path) and os.path.getsize(temp_path) > 0:
            # Atomic replacement keeps readers safe.  A duplicate request
            # may race here, but both files contain the same cache key and
            # the final result remains a complete image.
            os.replace(temp_path, path)
            return path
        return ""


class SubscriptionImageCache(SearchImageCache):
    """Cache subscription-page video covers under ``data/img/sub``.

    Search results and subscription results intentionally use different cache
    roots.  A cover fetched while browsing subscriptions must not overwrite or
    be mistaken for a cover produced by a download rule.
    """

    FOLDER = "sub"

    def __init__(self, root: str | None = None):
        super().__init__(root)
        # Keep the subscription cache directory visible even before the first
        # successful image request.  This also makes the configured storage
        # location unambiguous for users and diagnostics.
        os.makedirs(self.root, exist_ok=True)
