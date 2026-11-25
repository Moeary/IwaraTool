"""Add, import, pause and delete subscription sources."""

from __future__ import annotations

from PySide6.QtCore import Qt

from qfluentwidgets import InfoBarPosition

from ...presentation.subscription import text as sub_text
from ...presentation.subscription.source_actions import add_source_from_text, remove_sources
from .components import SubscriptionImportAuthorsWorker
from .page_dependencies import InfoBar, download_manager
from ..common.fluent_dialogs import show_fluent_confirmation, show_fluent_text_input


class SubscriptionSourceManagementMixin:
    """Add, import, pause and delete subscription sources."""

    def _add_source(self):
        text, ok = show_fluent_text_input(
            self,
            sub_text.add_title(),
            sub_text.add_prompt(),
            accept_text=sub_text.add_button(),
            cancel_text=sub_text.cancel_text(),
        )
        if not ok or not text.strip():
            return
        source_id = add_source_from_text(download_manager, text)
        if source_id is None:
            self._show_error(sub_text.invalid_input_text())
            return
        self._load_sources()
        if source_id:
            self._select_source_id(source_id)
            self._load_items(source_id)
            self._start_refresh(source_id)
            title, content = sub_text.added_notice()
            InfoBar.success(
                title=title,
                content=content,
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=2500,
                parent=self,
            )

    def _on_subscription_source_added(self, source_id: int):
        if self._shutting_down:
            return
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
        kind, title, content = sub_text.import_finished(result)
        show = InfoBar.warning if kind == "warning" else InfoBar.success
        show(
            title=title,
            content=content,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=5000 if kind == "warning" else 4000,
            parent=self,
        )

    def _delete_selected_source(self):
        self._delete_source_ids(self._selected_source_ids_for_operation())

    def _delete_source_ids(self, source_ids: list[int]):
        ids = list(dict.fromkeys(int(source_id) for source_id in source_ids if source_id))
        if not ids:
            self._show_error(sub_text.select_sources_first_text())
            return
        if self._worker and self._worker.isRunning():
            self._show_error(sub_text.wait_for_refresh_text())
            return
        question, content = self._delete_confirmation_text(ids)
        if not show_fluent_confirmation(
            self,
            sub_text.delete_title(),
            question,
            informative=sub_text.delete_informative(),
            yes_text=sub_text.delete_confirm_button(),
            no_text=sub_text.cancel_text(),
        ):
            return
        remove_sources(download_manager, ids)
        if self._current_source_id in ids:
            self._current_source_id = None
        self._load_sources()
        InfoBar.success(
            title=sub_text.deleted_title(),
            content=content,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=2500,
            parent=self,
        )

    def _delete_confirmation_text(self, ids: list[int]) -> tuple[str, str]:
        """The question and the short summary for deleting ``ids``."""

        return sub_text.delete_confirmation(self._sources, ids)

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
