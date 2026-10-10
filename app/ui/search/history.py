"""Recent searches shown under the keyword box."""

from __future__ import annotations

import json

from PySide6.QtCore import QPoint, QTimer

from ...config import app_config
from ...i18n import tr
from .widgets import _decode_search_history, _upsert_search_history


_SEARCH_HISTORY_KEY = "search_history_v1"


class SearchHistoryMixin:
    """Recent searches shown under the keyword box."""

    @staticmethod
    def _search_history_label(entry: dict[str, str]) -> str:
        keyword = entry.get("keyword", "") or tr(
            "Latest videos", "最新视频", "最新動画"
        )
        scope_labels = {
            "videos": tr("Videos", "视频", "動画"),
            "images": tr("Images", "图片", "画像"),
            "authors": tr("Authors", "作者", "作者"),
            "tags": tr("Tags", "标签搜索", "タグ検索"),
            "playlists": tr("Playlists", "播放列表", "プレイリスト"),
        }
        source_labels = {
            "oreno3d": tr("Oreno3D", "Oreno3D", "Oreno3D"),
            "iwara": tr("Iwara", "Iwara", "Iwara"),
        }
        scope = scope_labels.get(entry.get("scope", "videos"), entry.get("scope", "videos"))
        source = source_labels.get(entry.get("source", "oreno3d"), entry.get("source", "oreno3d"))
        return f"{keyword} · {scope} · {source}"

    def _read_search_history(self) -> list[dict[str, str]]:
        history = _decode_search_history(app_config.get_ui_value(_SEARCH_HISTORY_KEY, "[]"))
        return history[: app_config.search_history_limit]

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
        entry = {
            "keyword": self._keyword_edit.text().strip(),
            "source": str(self._source_combo.currentData() or "oreno3d"),
            "scope": str(self._scope_combo.currentData() or "videos"),
            "sort": str(self._sort_combo.currentData() or "date"),
        }
        history = _upsert_search_history(
            _decode_search_history(app_config.get_ui_value(_SEARCH_HISTORY_KEY, "[]")),
            entry,
            app_config.search_history_limit,
        )
        app_config.set_ui_value(
            _SEARCH_HISTORY_KEY,
            json.dumps(history, ensure_ascii=False, separators=(",", ":")),
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
        app_config.set_ui_value(_SEARCH_HISTORY_KEY, "[]")
        self._refresh_search_history_popup()
