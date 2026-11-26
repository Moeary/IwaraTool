"""Recent searches shown under the keyword box."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QTimer

from ...presentation.search.history_entries import _decode_search_history, clear_search_history, read_search_history, record_search, search_history_label



class SearchHistoryMixin:
    """Recent searches shown under the keyword box."""

    @staticmethod
    def _search_history_label(entry: dict[str, str]) -> str:
        return search_history_label(entry)

    def _read_search_history(self) -> list[dict[str, str]]:
        return read_search_history()

    def _refresh_search_history_popup(self):
        if not hasattr(self, "_search_history_popup"):
            return
        self._search_history_popup.set_history(
            self._read_search_history(),
            self._search_history_label,
        )

    def _show_search_history_popup(self):
        if not hasattr(self, "_search_history_popup"):
            return
        history = self._read_search_history()
        if not history:
            self._search_history_popup.hide()
            return
        self._search_history_popup.resize(
            min(620, max(360, self._keyword_edit.width())),
            min(280, max(60, self._search_history_popup.sizeHint().height())),
        )
        self._search_history_popup.move(
            self._keyword_edit.mapToGlobal(QPoint(0, self._keyword_edit.height()))
        )
        self._search_history_popup.show()
        self._search_history_popup.raise_()

    def _hide_search_history_popup(self):
        if hasattr(self, "_search_history_popup"):
            self._search_history_popup.hide()

    def _record_current_search(self):
        if self._author_video_target is not None:
            return
        record_search(
            self._keyword_edit.text(),
            str(self._source_combo.currentData() or "oreno3d"),
            str(self._scope_combo.currentData() or "videos"),
            str(self._sort_combo.currentData() or "date"),
        )
        self._refresh_search_history_popup()

    def _apply_search_history(self, payload: str):
        decoded = _decode_search_history(payload)
        if not decoded:
            return
        self._clear_author_navigation()
        entry = decoded[0]
        self._auto_search_timer.stop()
        self._set_combo_data(self._source_combo, entry["source"])
        self._set_combo_data(self._scope_combo, entry["scope"])
        self._keyword_edit.setText(entry["keyword"])
        self._set_combo_data(self._sort_combo, entry["sort"])
        self._auto_search_timer.stop()
        QTimer.singleShot(0, self._start_search)

    def _clear_search_history(self):
        clear_search_history()
        self._refresh_search_history_popup()
