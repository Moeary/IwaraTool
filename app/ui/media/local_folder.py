"""Shared context action for the actual file saved in download history."""

from __future__ import annotations

from qfluentwidgets import Action, FluentIcon, InfoBar, InfoBarPosition

from ...core.download.locations import resolve_record_location
from ...i18n import tr


def add_local_folder_action(menu, parent, manager, video_id: str):
    """Offer the action only for a local file; recheck history when clicked."""
    if not video_id:
        return
    record = manager.history.get_record(video_id)
    # A NAS copy counts only through its local mount: never "local" by itself.
    if not record or not resolve_record_location(record).local_path:
        return

    def open_folder():
        ok, message = manager.open_history_output(video_id, open_file=False)
        if not ok:
            InfoBar.warning(
                title=tr("Cannot open", "无法打开", "開けません"), content=message,
                isClosable=True, position=InfoBarPosition.TOP, duration=3000, parent=parent,
            )

    menu.addAction(Action(
        FluentIcon.FOLDER,
        tr("Open containing folder", "打开所在文件夹", "保存先フォルダーを開く"),
        parent, triggered=open_folder,
    ))
