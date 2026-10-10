"""Tag completion popup for the keyword box."""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, QTimer

from qfluentwidgets import LineEdit

from ...core.oreno3d_search import apply_tag_suggestion, tag_suggestion_query
from ...core.tag_dictionary import complete_tag_query, tag_query_fragment
from .page_dependencies import download_manager


class SearchTagSuggestionsMixin:
    """Tag completion popup for the keyword box."""

    def _show_tag_suggestions(self, edit: LineEdit, text: str):
        if self._tag_popup is None:
            return
        if str(text or "").strip():
            self._hide_search_history_popup()
        if str(self._scope_combo.currentData() or "videos") != "tags":
            self._tag_popup.hide()
            return
        query = (
            tag_query_fragment(text)
            if str(self._source_combo.currentData() or "oreno3d") == "iwara"
            else tag_suggestion_query(text)
        )
        if not query:
            self._tag_popup.hide()
            return
        suggestions = download_manager.get_search_tag_suggestions(query, limit=12)
        if not suggestions:
            self._tag_popup.hide()
            return
        self._active_tag_edit = edit
        self._tag_popup.set_suggestions(suggestions)
        self._tag_popup.resize(
            min(560, max(360, edit.width())),
            min(260, max(60, self._tag_popup.sizeHint().height())),
        )
        self._tag_popup.move(edit.mapToGlobal(QPoint(0, edit.height())))
        self._tag_popup.show()
        self._tag_popup.raise_()

    def _apply_tag_suggestion(self, key: str):
        edit = self._active_tag_edit
        if edit is None or str(self._scope_combo.currentData() or "videos") != "tags":
            return
        if self._tag_popup is not None:
            self._tag_popup.hide()
        complete = (
            complete_tag_query
            if str(self._source_combo.currentData() or "oreno3d") == "iwara"
            else apply_tag_suggestion
        )
        edit.setText(complete(edit.text(), key))
        edit.setFocus(Qt.FocusReason.OtherFocusReason)
        edit.setCursorPosition(len(edit.text()))
        QTimer.singleShot(0, self, lambda edit=edit: self._restore_tag_edit_focus(edit))

    def _restore_tag_edit_focus(self, edit: LineEdit):
        if edit is not self._active_tag_edit or not edit.isVisible() or not edit.isEnabled():
            return
        edit.setFocus(Qt.FocusReason.OtherFocusReason)
        edit.setCursorPosition(len(edit.text()))
