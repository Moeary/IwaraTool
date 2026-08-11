"""Small, atomic image cache used by search result cards."""

from __future__ import annotations

import hashlib
import os
import re
import threading
from urllib.parse import urlparse


class SearchImageCache:
    """Cache remote search images under ``data/img/search``.

    Files are written to a temporary sibling and atomically renamed.  This is
    important for the UI: a card never observes a partially downloaded image.
    """

    def __init__(self, root: str | None = None):
        if root is None:
            # Import lazily so this small utility stays easy to use in isolated
            # tests and does not make the config module import this package.
            from ..config import app_config

            root = os.path.join(app_config.app_data_dir, "img", "search")
        self.root = os.path.abspath(root)
        self._lock = threading.RLock()

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
    ) -> str:
        """Return a cached path, downloading the image when needed.

        Network and image validation failures intentionally return an empty
        string.  Search cards can keep their placeholder without failing the
        entire result page.
        """

        image_url = str(image_url or "").strip()
        if not image_url or not image_url.startswith(("http://", "https://")):
            return ""
        path = self.path_for(kind, item_key, image_url)
        with self._lock:
            try:
                if os.path.isfile(path) and os.path.getsize(path) > 0:
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
                    os.replace(temp_path, path)
                    return path
                return ""
            except Exception:
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
