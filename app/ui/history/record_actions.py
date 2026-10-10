"""Open, rename, remove and clean history records, and sync them with the download folder."""

from __future__ import annotations

import os
import webbrowser

from PySide6.QtCore import Qt

from qfluentwidgets import InfoBar, InfoBarPosition

from ...i18n import tr
from ..ui_state import show_fluent_confirmation, show_fluent_text_input
from .page_dependencies import download_manager
from .record_state import _video_url


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
            self._show_error(tr("History record does not exist", "历史记录不存在", "履歴が存在しません"))
            return

        file_path = str(record.get("file_path", "") or "")
        if not file_path or not os.path.isfile(file_path):
            self._show_error(tr("File does not exist", "文件不存在", "ファイルが存在しません"))
            return

        old_name = os.path.basename(file_path)
        new_name, ok = show_fluent_text_input(
            self,
            tr("Rename File", "重命名文件", "ファイル名を変更"),
            tr("New file name:", "新的文件名：", "新しいファイル名:"),
            text=old_name,
            accept_text=tr("Rename", "重命名", "名前を変更"),
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
            title=tr("Renamed", "已重命名", "名前変更完了"),
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
            self._show_error(tr("History record does not exist", "历史记录不存在", "履歴が存在しません"))
            return

        titles = [
            str(self._records_by_id[video_id].get("title", "") or video_id)
            for video_id in ids
        ]
        if len(ids) == 1:
            prompt = tr(
                f"Remove this DB record?\n{titles[0]}",
                f"删除这条数据库记录？\n{titles[0]}",
                f"このDB履歴を削除しますか？\n{titles[0]}",
            )
        else:
            preview = "\n".join(f"• {title}" for title in titles[:5])
            if len(titles) > 5:
                preview += tr("\n…", "\n…", "\n…")
            prompt = tr(
                f"Remove {len(ids)} selected DB records?\n{preview}",
                f"删除选中的 {len(ids)} 条数据库记录？\n{preview}",
                f"選択したDB履歴 {len(ids)} 件を削除しますか？\n{preview}",
            )
        confirmed = show_fluent_confirmation(
            self,
            tr("Remove Record", "删除记录", "履歴を削除"),
            prompt,
            informative=tr(
                "The local file will not be deleted. Removing this record lets the video be queued again.",
                "不会删除本地文件；删除记录后，该视频之后可以再次入队下载。",
                "ローカルファイルは削除されません。履歴を削除すると再度キューに追加できます。",
            ),
            yes_text=tr("Delete record", "删除记录", "履歴を削除"),
            no_text=tr("Cancel", "取消", "キャンセル"),
        )
        if not confirmed:
            return

        removed = download_manager.remove_history_records(ids)
        self._load_history()
        InfoBar.success(
            title=tr("History records removed", "历史记录已删除", "履歴を削除しました"),
            content=tr(
                f"Removed {removed} DB record(s); local files were left untouched.",
                f"已删除 {removed} 条数据库记录；本地文件未被删除。",
                f"DB履歴を {removed} 件削除しました。ローカルファイルは削除していません。",
            ),
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

        confirmed = show_fluent_confirmation(
            self,
            tr("Clean Selected Records", "清理选中记录", "選択した履歴を整理"),
            tr(
                f"Remove {len(moved_ids)} selected records whose files are missing or outside the download folder?",
                f"删除选中 {len(moved_ids)} 条文件缺失或已移出下载目录的记录？",
                f"ファイル不明または保存先外の選択履歴 {len(moved_ids)} 件を削除しますか？",
            ),
            informative=tr(
                "Local files will not be deleted.",
                "不会删除本地文件。",
                "ローカルファイルは削除されません。",
            ),
            yes_text=tr("Clean records", "清理记录", "履歴を整理"),
            no_text=tr("Cancel", "取消", "キャンセル"),
        )
        if not confirmed:
            return

        removed = download_manager.remove_history_records(moved_ids)
        self._load_history()
        InfoBar.success(
            title=tr("Cleanup Finished", "清理完成", "整理完了"),
            content=tr(
                f"Removed {removed} selected moved record(s).",
                f"已清理 {removed} 条选中的失效记录。",
                f"選択した移動済み履歴を {removed} 件整理しました。",
            ),
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
            InfoBar.success(
                title=tr("Nothing To Clean", "无需清理", "削除対象なし"),
                content=tr("No moved records found", "没有发现已移走记录", "移動済み履歴はありません"),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=2500,
                parent=self,
            )
            return

        confirmed = show_fluent_confirmation(
            self,
            tr("Clean Moved Records", "清理已移走记录", "移動済み履歴を削除"),
            tr(
                f"This will delete {len(moved_records)} DB records for files that are missing or outside the current download folder.",
                f"将删除 {len(moved_records)} 条文件缺失或不在当前下载文件夹内的数据库记录。",
                f"不明または保存先外のDB履歴 {len(moved_records)} 件を削除します。",
            ),
            informative=tr(
                "Local files will not be deleted, but these videos will no longer count as previously saved in history.",
                "不会删除本地文件，但这些视频之后不再被历史库视为已保存；后续可能会重新入队。",
                "ローカルファイルは削除されませんが、履歴上は保存済み扱いではなくなります。",
            ),
            yes_text=tr("Clean records", "清理记录", "履歴を削除"),
            no_text=tr("Cancel", "取消", "キャンセル"),
        )
        if not confirmed:
            return

        stats = download_manager.sync_history_with_download_folder()
        self._load_history()
        InfoBar.success(
            title=tr("Cleanup Finished", "清理完成", "削除完了"),
            content=tr(
                f"Kept {stats['kept']}, removed {stats['removed']}",
                f"保留 {stats['kept']} 条，删除数据库记录 {stats['removed']} 条",
                f"保持 {stats['kept']} 件、DB履歴を {stats['removed']} 件削除",
            ),
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=4000,
            parent=self,
        )
