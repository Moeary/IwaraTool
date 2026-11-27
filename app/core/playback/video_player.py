"""Choosing and launching the player for a video that exists on disk.

``builtin`` is handled by the UI (it needs a window); everything else is a
detached external process started here.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from typing import Callable

from ...config import app_config
from ...i18n import tr
from ...logging_setup import get_logger

logger = get_logger(__name__)

MODE_SYSTEM = "system"
MODE_BUILTIN = "builtin"
MODE_CUSTOM = "custom"
MODES = (MODE_SYSTEM, MODE_BUILTIN, MODE_CUSTOM)
FILE_PLACEHOLDER = "{file}"

# Phones have no system or custom player to hand a file to; the UI turns this
# off there so every "play" stays in the built-in player whatever Settings say.
_external_available = True


def set_external_available(available: bool) -> None:
    global _external_available
    _external_available = bool(available)


def player_mode() -> str:
    if not _external_available:
        return MODE_BUILTIN
    mode = str(app_config.preview_player_mode or MODE_SYSTEM).strip().lower()
    return mode if mode in MODES else MODE_SYSTEM


def external_mode() -> str:
    """How "play in external player" opens a file: the custom command when set up, else the system player."""
    return MODE_CUSTOM if player_mode() == MODE_CUSTOM else MODE_SYSTEM


def split_command(command: str, *, windows: bool | None = None) -> list[str]:
    """Split a user-entered command line, honouring quotes."""
    windows = (os.name == "nt") if windows is None else windows
    parts = shlex.split(command, posix=not windows)
    if windows:
        # non-POSIX shlex keeps the surrounding quotes.
        parts = [p[1:-1] if len(p) >= 2 and p[0] == p[-1] == '"' else p for p in parts]
    return parts


def build_custom_command(command: str, file_path: str, *, windows: bool | None = None) -> list[str]:
    """``command`` with ``{file}`` replaced, or the file appended when absent."""
    parts = split_command(str(command or "").strip(), windows=windows)
    if not parts:
        return []
    if any(FILE_PLACEHOLDER in part for part in parts):
        return [part.replace(FILE_PLACEHOLDER, file_path) for part in parts]
    return [*parts, file_path]


def is_url(target: str) -> bool:
    return str(target or "").strip().lower().startswith(("http://", "https://"))


def _history(history):
    if history is None:
        from ..download.manager import download_manager

        history = download_manager.history
    return history


def video_location(video_id: str, history=None):
    """Where the downloaded copy of ``video_id`` is (see ``download.locations``)."""
    from ..download.locations import resolve_record_location

    video_id = str(video_id or "").strip()
    if not video_id:
        return resolve_record_location(None)
    return resolve_record_location(_history(history).get_record(video_id))


def local_video_path(video_id: str, history=None) -> str:
    """A file this machine can open for ``video_id``: the local download, or the NAS copy through its mount."""
    return video_location(video_id, history).local_path


def playable_video_target(video_id: str, history=None) -> str:
    """``local_video_path``, else an http(s) URL of the copy on the aria2 host, else ``""``."""
    local = local_video_path(video_id, history)
    if local or not str(app_config.aria2_remote_http_base or "").strip():
        return local
    return video_location(video_id, history).stream_url


def downloaded_copy(video_id: str, manager) -> str:
    """``playable_video_target``, else a finished temporary copy (search / detail downloads keep no history row)."""
    target = playable_video_target(video_id, manager.history)
    if target:
        return target
    find = getattr(manager, "temporary_copy_path", None)
    return find(video_id) if callable(find) else ""


def opens_externally(target: str, mode: str | None = None) -> bool:
    """True when ``target`` goes to an external player; False means the built-in window plays it.

    The system handler of an http URL is the web browser, not a player, so a
    URL only goes out when a custom player command is set.
    """
    mode = mode or player_mode()
    if mode == MODE_BUILTIN or not _external_available:
        return False
    if is_url(target):
        return mode == MODE_CUSTOM
    return True


def open_local_video(
    path: str,
    *,
    mode: str | None = None,
    launcher: Callable[[list[str]], None] | None = None,
) -> tuple[bool, str]:
    """Open ``path`` in the system or custom player (``builtin`` falls back to system)."""
    url = is_url(path)
    if not path or (not url and not os.path.isfile(path)):
        return False, tr("File does not exist", "文件不存在", "ファイルが存在しません")
    mode = mode or player_mode()
    if url and mode != MODE_CUSTOM:
        return False, tr(
            "Streaming a NAS copy needs the built-in player or a custom player command",
            "播放远端文件需要使用内置播放器或自定义播放器命令",
            "リモートのファイルを再生するには内蔵プレイヤーかカスタムコマンドが必要です",
        )
    try:
        if mode == MODE_CUSTOM:
            command = build_custom_command(app_config.preview_player_command, path)
            if not command:
                return False, tr(
                    "No custom player command is set in Settings",
                    "设置中尚未填写自定义播放器命令",
                    "設定でカスタムプレイヤーのコマンドが未設定です",
                )
            (launcher or _spawn)(command)
            return True, ""
        _open_with_system(path)
        return True, ""
    except Exception as exc:
        logger.warning("Could not open %s", path, exc_info=True)
        return False, str(exc)


def _spawn(command: list[str]) -> None:
    kwargs: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["creationflags"] = 0x00000008 | 0x08000000  # DETACHED_PROCESS | CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(command, **kwargs)


def _open_with_system(path: str) -> None:
    if os.name == "nt":
        os.startfile(path)  # noqa: S606 - user-initiated, local file
    elif sys.platform == "darwin":
        _spawn(["/usr/bin/open", path])
    elif shutil.which("xdg-open"):
        subprocess.Popen(["xdg-open", path])
    elif shutil.which("open"):
        subprocess.Popen(["open", path])
    else:
        raise OSError(
            tr(
                "System does not support auto-open",
                "系统不支持自动打开",
                "システムが自動オープンに対応していません",
            )
        )
