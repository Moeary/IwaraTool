"""Ask where to save a file the app makes (a backup, a CSV export) and write it there.

On the desktop the save dialog returns a path and the app writes the file.
On phones the platform dialog writes the file itself and needs its contents
up front (Flet's ``save_file`` raises without ``src_bytes`` there), so the file
is made in a temporary folder first and its bytes handed to the dialog.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypeVar

import flet as ft

T = TypeVar("T")


async def save_made_file(shell, write: Callable[[str], Awaitable[T]], *, dialog_title: str, file_name: str,
                         extensions: list[str], **picker_options: Any) -> tuple[str, T] | None:
    """``write(path)`` makes the file; returns ``(where it was saved, what write returned)``, None if cancelled."""
    picker = ft.FilePicker()
    options = dict(dialog_title=dialog_title, file_name=file_name, allowed_extensions=extensions,
                   file_type=ft.FilePickerFileType.CUSTOM, **picker_options)
    if not shell.caps.mobile:
        path = await picker.save_file(**options)
        if not path:
            return None
        return path, await write(path)
    with tempfile.TemporaryDirectory(prefix="iwaratool-save-") as folder:
        made = os.path.join(folder, file_name)
        result = await write(made)
        data = await asyncio.to_thread(Path(made).read_bytes)
        options.pop("initial_directory", None)  # a desktop folder means nothing to the phone's dialog
        path = await picker.save_file(src_bytes=data, **options)
    if not path:
        return None
    return path, result
