"""Open, rename, remove and clean history records, and sync them with the download folder."""

from __future__ import annotations

import os
import webbrowser

from PySide6.QtCore import Qt

from qfluentwidgets import InfoBar, InfoBarPosition

from ...i18n import tr
from ..common.fluent_dialogs import show_fluent_confirmation, show_fluent_text_input
from .page_dependencies import download_manager
from ...presentation.history import library
from ...presentation.history.record_state import _video_url


class HistoryRecordActionsMixin:
    """Open, rename, remove and clean history records, and sync them with the download folder."""

    def _remove_selected_record(self):
        self._remove_records(self._selected_video_ids())

    def _open_selected(self, *, open_file: bool):
        video_id = self._selected_video_id()
        if video_id:
            self._open_record(video_id, open_file=open_file)

    def _open_record(self, video_id: str, *, open_file: bool):
        ok, msg = download_manager.open_history_output(video_id, open_file=open_file)
        if not ok:
            self._show_error(msg)

    def _open_video_url(self, video_id: str, *, url: str = ""):
        record = self._records_by_id.get(video_id, {})
        url = str(url or record.get("source_url", "") or _video_url(video_id))
        if url:
            webbrowser.open(url)

    def _rename_record(self, video_id: str):
        record = self._records_by_id.get(video_id)
        if not record:
            self._show_error(library.missing_record_text())
            return

        file_path = str(record.get("file_path", "") or "")
        if not file_path or not os.path.isfile(file_path):
            self._show_error(library.missing_file_text())
            return

        old_name = os.path.basename(file_path)
        title, label, button = library.rename_texts()
        new_name, ok = show_fluent_text_input(
            self,
            title,
            label,
            text=old_name,
            accept_text=button,
            cancel_text=tr("Cancel", "取消", "キャンセル"),
        )
        if not ok or not new_name.strip():
            return

        renamed, msg = download_manager.rename_history_file(video_id, new_name)
        if not renamed:
            self._show_error(msg)
            return

        self._load_history()
        InfoBar.success(
            title=library.renamed_title(),
            content=msg,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=3000,
            parent=self,
        )

    def _remove_record(self, video_id: str):
        self._remove_records([video_id])

    def _remove_records(self, video_ids: list[str]):
        ids = list(
            dict.fromkeys(
                str(video_id or "").strip()
                for video_id in video_ids
                if str(video_id or "").strip() in self._records_by_id
            )
        )
        if not ids:
            self._show_error(library.missing_record_text())
            return

        titles = [
            str(self._records_by_id[video_id].get("title", "") or video_id)
            for video_id in ids
        ]
        prompt = library.remove_prompt(titles)
        confirmed = show_fluent_confirmation(
            self,
            library.remove_title(),
            prompt,
            informative=library.remove_informative(),
            yes_text=library.remove_button(),
            no_text=tr("Cancel", "取消", "キャンセル"),
        )
        if not confirmed:
            return

        removed = download_manager.remove_history_records(ids)
        self._load_history()
        title, content = library.removed_notice(removed)
        InfoBar.success(
            title=title,
            content=content,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=2200,
            parent=self,
        )

    def _clean_selected_records(self, video_ids: list[str]):
        moved_ids = [
            video_id
            for video_id in video_ids
            if video_id in self._records_by_id
            and self._record_state(self._records_by_id[video_id])[1] == "moved"
        ]
        if not moved_ids:
            return

        title, question, informative, button = library.clean_selected_texts(len(moved_ids))
        confirmed = show_fluent_confirmation(
            self, title, question, informative=informative, yes_text=button, no_text=tr("Cancel", "取消", "キャンセル"),
        )
        if not confirmed:
            return

        removed = download_manager.remove_history_records(moved_ids)
        self._load_history()
        title, content = library.cleaned_selected_notice(removed)
        InfoBar.success(
            title=title,
            content=content,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=3000,
            parent=self,
        )

    def _sync_with_download_folder(self):
        moved_records = [
            row
            for row in self._all_records
            if self._record_state(row)[1] == "moved"
        ]
        if not moved_records:
            title, content = library.nothing_to_clean_notice()
            InfoBar.success(
                title=title,
                content=content,
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=2500,
                parent=self,
            )
            return

        title, question, informative, button = library.sync_texts(len(moved_records))
        confirmed = show_fluent_confirmation(
            self, title, question, informative=informative, yes_text=button, no_text=tr("Cancel", "取消", "キャンセル"),
        )
        if not confirmed:
            return

        stats = download_manager.sync_history_with_download_folder()
        self._load_history()
        title, content = library.synced_notice(stats)
        InfoBar.success(
            title=title,
            content=content,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=4000,
            parent=self,
        )
