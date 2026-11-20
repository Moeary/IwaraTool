"""Shared building blocks for every video right-click menu.

All pages put a single video's entries in the same order:

1. View details
2. Play (labelled by where the copy is: local file / NAS copy / online)
   · Open containing folder · Copy file path
3. Download (page specific)
4. Open in browser · Copy link
5. Author entries (page specific)
6. Page management entries (pending, history, tasks …)

The helpers below add groups 1, 2 and 4; :func:`add_section` puts a separator
between groups without ever leaving two in a row.
"""

from __future__ import annotations

import webbrowser
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from qfluentwidgets import Action, FluentIcon, InfoBar, InfoBarPosition, RoundMenu

from ...core.download.history_actions import remote_unreachable_message
from ...core.download.locations import FileLocation, resolve_record_location
from ...i18n import tr
from ...signal_bus import signal_bus


def add_section(menu: RoundMenu) -> None:
    """Separate the next group from the previous one (no leading / doubled separators)."""

    view = getattr(menu, "view", None)
    if view is None:  # a plain QMenu
        actions = menu.actions()
        if actions and not actions[-1].isSeparator():
            menu.addSeparator()
        return
    # RoundMenu draws separators as list items tagged "seperator" (sic), not QActions.
    count = view.count()
    if not count:
        return
    last = view.item(count - 1)
    if last is not None and last.data(Qt.ItemDataRole.DecorationRole) == "seperator":
        return
    menu.addSeparator()


def record_location(manager, video_id: str) -> FileLocation:
    if not video_id:
        return FileLocation()
    try:
        return resolve_record_location(manager.history.get_record(video_id))
    except Exception:
        return FileLocation()


def _warn(parent, message: str) -> None:
    InfoBar.warning(
        title=tr("Cannot open", "无法打开", "開けません"), content=message,
        isClosable=True, position=InfoBarPosition.TOP, duration=4000, parent=parent,
    )


def copy_text(parent, text: str, *, what: str = "") -> None:
    QApplication.clipboard().setText(str(text or ""))
    InfoBar.success(
        title=tr("Copied", "已复制", "コピーしました"), content=what or str(text or ""),
        isClosable=True, position=InfoBarPosition.TOP, duration=1800, parent=parent,
    )


def play_label(location: FileLocation, *, can_stream: bool = True) -> str:
    if location.local_path and location.is_remote:
        return tr("Play NAS copy (mounted)", "播放远端文件（挂载）", "リモートのファイルを再生（マウント）")
    if location.local_path:
        return tr("Play local file", "播放本地文件", "ローカルファイルを再生")
    if location.stream_url:
        return tr("Play NAS copy", "播放远端文件", "リモートのファイルを再生")
    if can_stream:
        return tr("Play online", "在线播放", "オンライン再生")
    return ""


def add_detail_action(menu: RoundMenu, parent, callback: Callable[[], None]) -> None:
    menu.addAction(Action(
        FluentIcon.VIEW, tr("View details", "查看详情", "詳細を表示"), parent,
        triggered=lambda _checked=False: callback(),
    ))


def add_play_actions(
    menu: RoundMenu,
    parent,
    manager,
    video_id: str,
    *,
    title: str = "",
    can_stream: bool = True,
    play: Callable[[], None] | None = None,
    location: FileLocation | None = None,
) -> FileLocation:
    """Play / open folder / copy path for one video, by where its downloaded copy is.

    ``play`` overrides how playback starts (Search resolves Oreno3D results
    first); by default the preview request picks the local file, the NAS copy
    or the online stream in that order.
    """

    location = location if location is not None else record_location(manager, video_id)
    label = play_label(location, can_stream=can_stream)
    if label:
        def start():
            if play is not None:
                play()
            else:
                signal_bus.video_preview_requested.emit(video_id, title, location.playable_target)

        menu.addAction(Action(FluentIcon.PLAY, label, parent, triggered=lambda _checked=False: start()))

    if location.local_path:
        def open_folder():
            ok, message = manager.open_history_output(video_id, open_file=False)
            if not ok:
                _warn(parent, message)

        menu.addAction(Action(
            FluentIcon.FOLDER, tr("Open containing folder", "打开所在文件夹", "保存先フォルダーを開く"), parent,
            triggered=lambda _checked=False: open_folder(),
        ))
        menu.addAction(Action(
            FluentIcon.COPY, tr("Copy file path", "复制文件路径", "ファイルパスをコピー"), parent,
            triggered=lambda _checked=False, path=location.local_path: copy_text(parent, path),
        ))
    elif location.is_remote and location.remote_path:
        menu.addAction(Action(
            FluentIcon.FOLDER, tr("Open containing folder (NAS)", "打开所在文件夹（远端）", "保存先フォルダーを開く（リモート）"), parent,
            triggered=lambda _checked=False: _warn(parent, remote_unreachable_message(location, for_folder=True)),
        ))
        menu.addAction(Action(
            FluentIcon.COPY, tr("Copy NAS path", "复制远端路径", "リモートのパスをコピー"), parent,
            triggered=lambda _checked=False, path=location.remote_path: copy_text(parent, path),
        ))
    return location


def add_link_actions(menu: RoundMenu, parent, url: str) -> None:
    url = str(url or "").strip()
    if not url:
        return
    menu.addAction(Action(
        FluentIcon.GLOBE, tr("Open in browser", "在浏览器打开", "ブラウザーで開く"), parent,
        triggered=lambda _checked=False: webbrowser.open(url),
    ))
    menu.addAction(Action(
        FluentIcon.LINK, tr("Copy link", "复制链接", "リンクをコピー"), parent,
        triggered=lambda _checked=False: copy_text(parent, url),
    ))


__all__ = [
    "add_detail_action",
    "add_link_actions",
    "add_play_actions",
    "add_section",
    "copy_text",
    "play_label",
    "record_location",
]
