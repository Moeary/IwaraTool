"""Loopback HTTP proxy that lets the media player stream an Iwara file.

QMediaPlayer cannot attach an ``Authorization`` header, send the user's proxy
settings or re-sign an expired URL. The player therefore talks to
``http://127.0.0.1:<port>/s/<token>`` and this server forwards each Range
request upstream with the right headers.
"""
from __future__ import annotations

import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

from ..logging_setup import get_logger

logger = get_logger(__name__)

# Upstream headers that describe the byte range / body and must reach the player.
_RELAYED_HEADERS = ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges", "Last-Modified", "ETag")
_CHUNK = 64 * 1024


class StreamSource:
    """One streamable file: how to fetch it and how to refresh its URL."""

    def __init__(
        self,
        url: str,
        *,
        refresh: Callable[[], str] | None = None,
        headers: Callable[[], dict[str, str]] | None = None,
        proxies: Callable[[], dict[str, str] | None] | None = None,
    ):
        self.url = url
        self.refresh = refresh
        self.headers = headers or (lambda: {})
        self.proxies = proxies or (lambda: None)
        self._lock = threading.Lock()

    def renew(self, failed_url: str) -> str:
        """Fresh URL after a 401/403/410; only one caller refreshes at a time."""
        with self._lock:
            if self.url != failed_url or self.refresh is None:
                return self.url
            self.url = self.refresh() or self.url
            return self.url


class StreamProxy:
    def __init__(self, session_factory: Callable[[], Any]):
        self._session_factory = session_factory
        self._sources: dict[str, StreamSource] = {}
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # ── lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> int:
        with self._lock:
            if self._server is None:
                proxy = self

                class Handler(_Handler):
                    owner = proxy

                server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
                server.daemon_threads = True
                self._server = server
                self._thread = threading.Thread(
                    target=server.serve_forever, name="iwara-stream-proxy", daemon=True
                )
                self._thread.start()
            return self._server.server_address[1]

    def stop(self) -> None:
        with self._lock:
            server, self._server = self._server, None
            self._sources.clear()
        if server is not None:
            server.shutdown()
            server.server_close()

    # ── sources ──────────────────────────────────────────────────────────────

    def register(self, source: StreamSource) -> str:
        """Add ``source`` and return the loopback URL to hand to the player."""
        port = self.start()
        token = secrets.token_urlsafe(16)
        with self._lock:
            self._sources[token] = source
        return f"http://127.0.0.1:{port}/s/{token}"

    def unregister(self, url: str) -> None:
        """Drop the source behind one URL that ``register`` returned."""
        token = str(url or "").rsplit("/s/", 1)[-1].split("?", 1)[0]
        with self._lock:
            self._sources.pop(token, None)

    def unregister_all(self) -> None:
        with self._lock:
            self._sources.clear()

    def source_for(self, path: str) -> StreamSource | None:
        prefix = "/s/"
        if not path.startswith(prefix):
            return None
        with self._lock:
            return self._sources.get(path[len(prefix):].split("?", 1)[0])


class _Handler(BaseHTTPRequestHandler):
    owner: StreamProxy
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):  # noqa: A002 - silence default stderr logging
        logger.debug("proxy: " + format, *args)

    def do_HEAD(self):
        self._serve(head_only=True)

    def do_GET(self):
        self._serve(head_only=False)

    def _serve(self, *, head_only: bool) -> None:
        source = self.owner.source_for(self.path)
        if source is None:
            self.send_error(404)
            return
        range_header = self.headers.get("Range")
        session = self.owner._session_factory()
        response = None
        try:
            url = source.url
            for attempt in (0, 1):
                response = self._fetch(session, source, url, range_header, head_only)
                if response.status_code in (401, 403, 410) and attempt == 0:
                    response.close()
                    renewed = source.renew(url)
                    if renewed == url:
                        break
                    url = renewed
                    continue
                break
            self.send_response(response.status_code)
            for name in _RELAYED_HEADERS:
                value = response.headers.get(name)
                if value is not None:
                    self.send_header(name, value)
            self.send_header("Connection", "close")
            self.end_headers()
            if head_only or response.status_code in (204, 304):
                return
            for chunk in response.iter_content(chunk_size=_CHUNK):
                if chunk:
                    self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass  # the player closed the connection (seek / stop)
        except Exception:
            logger.warning("Stream proxy failed", exc_info=True)
            try:
                self.send_error(502)
            except Exception:
                pass
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass
            self.close_connection = True

    @staticmethod
    def _fetch(session, source: StreamSource, url: str, range_header: str | None, head_only: bool):
        headers = dict(source.headers())
        if range_header:
            headers["Range"] = range_header
        # Upstream HEAD is often rejected by signed URLs; ask for one byte.
        if head_only and not range_header:
            headers["Range"] = "bytes=0-0"
        response = session.get(url, headers=headers, stream=True, timeout=30, proxies=source.proxies())
        if head_only and not range_header and response.status_code == 206:
            # Report the full resource like a real HEAD would.
            total = str(response.headers.get("Content-Range", "")).rsplit("/", 1)[-1]
            if total.isdigit():
                response.headers["Content-Length"] = total
                response.headers.pop("Content-Range", None)
                response.status_code = 200
        return response
