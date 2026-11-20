"""Where a downloaded video lives: this machine, or the aria2 host (NAS / server).

A history row written by a remote aria2 download keeps ``file_path`` empty and
stores the path *as the aria2 host sees it* in ``remote_path``.  Code that only
knows ``file_path`` therefore never mistakes a NAS copy for a local file; code
that wants to open it asks :func:`resolve_record_location`, which maps the
remote path onto a local mount or an HTTP URL from the current settings.
"""

from __future__ import annotations

import ntpath
import os
import posixpath
import re
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import quote

STORAGE_LOCAL = "local"
STORAGE_REMOTE = "remote"
STORAGES = (STORAGE_LOCAL, STORAGE_REMOTE)

_WINDOWS_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")


def normalize_storage(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in STORAGES else STORAGE_LOCAL


def _config(cfg=None):
    if cfg is None:
        from ...config import app_config

        return app_config
    return cfg


def _is_windows_path(path: str) -> bool:
    return bool(_WINDOWS_PATH.match(path)) or ("\\" in path and "/" not in path)


def _pathmod(path: str):
    return ntpath if _is_windows_path(path) else posixpath


def remote_mode_enabled(cfg=None) -> bool:
    """aria2 RPC is on, marked as running elsewhere, and knows where to save."""

    cfg = _config(cfg)
    return bool(
        cfg.aria2_rpc_enabled
        and cfg.aria2_remote_enabled
        and str(cfg.aria2_remote_dir or "").strip()
    )


def join_remote_path(remote_root: str, relative_path: str) -> str:
    """``relative_path`` (built for this OS) under the aria2 host's ``remote_root``."""

    root = str(remote_root or "").strip()
    module = _pathmod(root)
    parts = [part for part in re.split(r"[\\/]+", str(relative_path or "")) if part and part != "."]
    if not root:
        return "/".join(parts)
    return module.join(root, *parts) if parts else root


def remote_relative_path(remote_path: str, remote_root: str) -> str | None:
    """``remote_path`` relative to ``remote_root`` with ``/`` separators, or None when outside."""

    path = str(remote_path or "").strip()
    root = str(remote_root or "").strip()
    if not path or not root:
        return None
    module = _pathmod(root)
    windows = module is ntpath
    norm_path = module.normpath(path.replace("/", "\\") if windows else path)
    norm_root = module.normpath(root.replace("/", "\\") if windows else root)
    cmp_path = norm_path.casefold() if windows else norm_path
    cmp_root = norm_root.casefold() if windows else norm_root
    sep = "\\" if windows else "/"
    if cmp_path == cmp_root:
        return ""
    prefix = cmp_root if cmp_root.endswith(sep) else cmp_root + sep
    if not cmp_path.startswith(prefix):
        return None
    rel = norm_path[len(prefix):]
    return rel.replace("\\", "/")


def mapped_local_path(remote_path: str, cfg=None) -> str:
    """The local mount path of ``remote_path`` (may not exist), or ``""``."""

    cfg = _config(cfg)
    mount = str(cfg.aria2_remote_mount_dir or "").strip()
    if not mount:
        return ""
    rel = remote_relative_path(remote_path, cfg.aria2_remote_dir)
    if rel is None:
        return ""
    parts = [part for part in rel.split("/") if part]
    return os.path.join(mount, *parts) if parts else mount


def remote_stream_url(remote_path: str, cfg=None) -> str:
    """An HTTP URL for ``remote_path`` under the configured base URL, or ``""``."""

    cfg = _config(cfg)
    base = str(cfg.aria2_remote_http_base or "").strip()
    if not base or not re.match(r"^https?://", base, re.IGNORECASE):
        return ""
    rel = remote_relative_path(remote_path, cfg.aria2_remote_dir)
    if not rel:
        return ""
    return base.rstrip("/") + "/" + "/".join(quote(part) for part in rel.split("/") if part)


def split_remote_path(remote_path: str) -> tuple[str, str]:
    """``(folder, file name)`` of a path on the aria2 host."""

    module = _pathmod(remote_path)
    return module.split(remote_path)


def remote_sidecar_path(remote_path: str, extension: str) -> str:
    """``remote_path`` with its extension replaced (``.jpg``, ``.nfo``)."""

    module = _pathmod(remote_path)
    return module.splitext(remote_path)[0] + extension


@dataclass(frozen=True, slots=True)
class FileLocation:
    """Where one history row's video is and how this machine can reach it."""

    storage: str = STORAGE_LOCAL
    file_path: str = ""  # local file recorded by a local download
    remote_path: str = ""  # path on the aria2 host
    local_path: str = ""  # a file this machine can open right now
    stream_url: str = ""  # HTTP URL of the remote file

    @property
    def is_remote(self) -> bool:
        return self.storage == STORAGE_REMOTE

    @property
    def accessible(self) -> bool:
        """Local file, or the remote file through its mount."""

        return bool(self.local_path)

    @property
    def playable_target(self) -> str:
        return self.local_path or self.stream_url

    @property
    def folder(self) -> str:
        return os.path.dirname(self.local_path) if self.local_path else ""

    @property
    def display_path(self) -> str:
        return self.remote_path if self.is_remote else self.file_path


def _is_file(path: str) -> bool:
    try:
        return bool(path) and os.path.isfile(path)
    except OSError:
        return False


def resolve_record_location(record: Mapping[str, Any] | None, cfg=None) -> FileLocation:
    if not record:
        return FileLocation()
    storage = normalize_storage(record.get("storage"))
    if storage == STORAGE_REMOTE:
        remote_path = str(record.get("remote_path", "") or "").strip()
        if not remote_path:
            return FileLocation(storage=storage)
        cfg = _config(cfg)
        mapped = mapped_local_path(remote_path, cfg)
        return FileLocation(
            storage=storage,
            remote_path=remote_path,
            local_path=mapped if _is_file(mapped) else "",
            stream_url=remote_stream_url(remote_path, cfg),
        )
    file_path = str(record.get("file_path", "") or "").strip()
    return FileLocation(
        storage=storage,
        file_path=file_path,
        local_path=file_path if _is_file(file_path) else "",
    )


def record_is_remote(record: Mapping[str, Any] | None) -> bool:
    return bool(record) and normalize_storage(record.get("storage")) == STORAGE_REMOTE


__all__ = [
    "FileLocation",
    "STORAGE_LOCAL",
    "STORAGE_REMOTE",
    "join_remote_path",
    "mapped_local_path",
    "normalize_storage",
    "record_is_remote",
    "remote_mode_enabled",
    "remote_relative_path",
    "remote_sidecar_path",
    "remote_stream_url",
    "resolve_record_location",
    "split_remote_path",
]
