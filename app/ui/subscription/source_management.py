"""Add, import, pause and delete subscription sources."""

from __future__ import annotations

from PySide6.QtCore import Qt

from qfluentwidgets import InfoBarPosition

from ...i18n import tr
from .components import SubscriptionImportAuthorsWorker
from .helpers import _detect_source_input
from .page_dependencies import InfoBar, download_manager
from ..fluent_dialogs import show_fluent_confirmation, show_fluent_text_input


class SubscriptionSourceManagementMixin:
    """Add, import, pause and delete subscription sources."""

    def _add_source(self):
        text, ok = show_fluent_text_input(
            self,
            tr("Add Subscription", "添加订阅", "購読を追加"),
            tr(
                "Author username / author URL / playlist URL:",
                "作者用户名 / 作者主页链接 / 播放列表链接：",
                "作者ユーザー名 / 作者URL / プレイリストURL:",
            ),
            accept_text=tr("Add", "添加", "追加"),
            cancel_text=tr("Cancel", "取消", "キャンセル"),
        )
        if not ok or not text.strip():
            return
        kind, key = _detect_source_input(text.strip())
        if not key:
            self._show_error(tr("Invalid subscription input", "订阅输入无效", "購読入力が不正です"))
            return
        if kind == "playlist":
            source_id = download_manager.add_playlist_subscription(key)
        else:
            source_id = download_manager.add_author_subscription(key)
        self._load_sources()
        if source_id:
            self._select_source_id(source_id)
            self._load_items(source_id)
            self._start_refresh(source_id)
            InfoBar.success(
                title=tr("Subscription Added", "订阅已添加", "購読を追加しました"),
                content=tr("Refreshing this source now", "正在立即刷新该订阅源", "この購読元を更新しています"),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=2500,
                parent=self,
            )

    def _on_subscription_source_added(self, source_id: int):
        source_id = int(source_id or 0)
        if not source_id:
            return
        if hasattr(self, "_source_search_edit") and self._source_search_edit.text():
            self._source_search_edit.blockSignals(True)
            self._source_search_edit.clear()
            self._source_search_edit.blockSignals(False)
        self._all_sources = self._fetch_sources()
        self._apply_source_filters(preferred_source_id=source_id)
        self._start_refresh(source_id)

    def _on_sources_changed_elsewhere(self):
        """Another page (author status bar, detail page) removed a subscription."""

        if not self._shutting_down:
            self._load_sources()

    def _import_followed_authors(self):
        if self._import_worker and self._import_worker.isRunning():
            return
        worker = SubscriptionImportAuthorsWorker()
        worker.result_ready.connect(self._on_import_followed_finished)
        worker.finished.connect(lambda worker=worker: self._release_worker("_import_worker", worker))
        self._import_worker = worker
        worker.start()

    def _release_worker(self, attr: str, worker):
        """Drop a page-owned QThread once its native ``finished`` has fired."""

        if getattr(self, attr, None) is worker:
            setattr(self, attr, None)
        worker.deleteLater()

    def _on_import_followed_finished(self, result: dict):
        self._load_sources()
        imported = int(result.get("imported", 0) or 0)
        error = str(result.get("error", "") or "")
        if error:
            InfoBar.warning(
                title=tr("Import Finished", "导入完成", "取込完了"),
                content=error,
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=5000,
                parent=self,
            )
            return
        content = tr(
            f"Synced {imported} authors",
            f"已同步 {imported} 个作者",
            f"{imported} 人の作者を同期しました",
        )
        InfoBar.success(
            title=tr("Followed Authors Imported", "关注作者已导入", "フォロー作者を取込"),
            content=content,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=4000,
            parent=self,
        )

    def _delete_selected_source(self):
        self._delete_source_ids(self._selected_source_ids_for_operation())

    def _delete_source_ids(self, source_ids: list[int]):
        ids = list(dict.fromkeys(int(source_id) for source_id in source_ids if source_id))
        if not ids:
            self._show_error(
                tr(
                    "Select one or more subscriptions first",
                    "请先选择一个或多个订阅",
                    "先に1つ以上の購読を選択してください",
                )
            )
            return
        if self._worker and self._worker.isRunning():
            self._show_error(
                tr(
                    "Wait for the current refresh to finish first",
                    "请先等待当前刷新完成",
                    "現在の更新が完了するまでお待ちください",
                )
            )
            return
        question, content = self._delete_confirmation_text(ids)
        if not show_fluent_confirmation(
            self,
            tr("Delete Subscription", "删除订阅", "購読を削除"),
            question,
            informative=tr(
                "Downloaded files and history records will not be deleted.",
                "不会删除已下载文件和历史记录。",
                "保存済みファイルと履歴は削除されません。",
            ),
            yes_text=tr("Delete subscription", "删除订阅", "購読を削除"),
            no_text=tr("Cancel", "取消", "キャンセル"),
        ):
            return
        remove_many = getattr(download_manager, "remove_subscription_sources", None)
        if callable(remove_many):
            remove_many(ids)
        else:
            for source_id in ids:
                download_manager.remove_subscription_source(source_id)
        if self._current_source_id in ids:
            self._current_source_id = None
        self._load_sources()
        InfoBar.success(
            title=tr("Subscription Deleted", "订阅已删除", "購読を削除しました"),
            content=content,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=2500,
            parent=self,
        )

    def _delete_confirmation_text(self, ids: list[int]) -> tuple[str, str]:
        """The question and the short summary for deleting ``ids``."""

        source_by_id = {
            int(source.get("id", 0) or 0): source
            for source in self._sources
            if int(source.get("id", 0) or 0)
        }
        names = [
            (
                str(source_by_id.get(source_id, {}).get("title", "") or "").strip()
                or str(source_by_id.get(source_id, {}).get("source_key", "") or "").strip()
                or f"#{source_id}"
            )
            for source_id in ids
        ]
        if len(ids) == 1:
            question = tr(
                f'Delete subscription "{names[0]}" and its cached video list?',
                f'确定删除订阅“{names[0]}”及其缓存视频列表吗？',
                f'購読「{names[0]}」と保存済み動画一覧を削除しますか？',
            )
            content = names[0]
        else:
            preview = "、".join(names[:3])
            if len(names) > 3:
                preview += tr(" and more", "等", "ほか")
            question = tr(
                f"Delete {len(ids)} selected subscriptions and their cached video lists?",
                f"确定删除选中的 {len(ids)} 个订阅及其缓存视频列表吗？",
                f"選択した {len(ids)} 件の購読と保存済み動画一覧を削除しますか？",
            )
            content = tr(
                f"Selected: {preview}",
                f"选中：{preview}",
                f"選択: {preview}",
            )
        return question, content

    def _toggle_selected_source(self):
        source_id = self._selected_source_id()
        if not source_id:
            return
        source = next((s for s in self._sources if int(s.get("id", 0) or 0) == source_id), None)
        if not source:
            return
        enabled = bool(int(source.get("enabled", 1) or 0))
        download_manager.set_subscription_enabled(source_id, not enabled)
        self._refresh_sources_keep_current_items()
