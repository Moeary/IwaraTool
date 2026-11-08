"""Post-search filter: a button in the options row opens it as a popup."""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDialog, QWidget

from qfluentwidgets import FluentIcon, TogglePushButton

from ...core.search.models import SearchVideo
from ...core.search.post_filter import PostFilter, apply_post_filter
from ...i18n import tr
from .post_filter_dialog import SearchPostFilterDialog


class SearchPostFilterMixin:
    """Filter and re-sort loaded video results without another request."""

    _post_filter: PostFilter = PostFilter()

    def _build_post_filter_button(self, parent: QWidget) -> TogglePushButton:
        self._post_filter_btn = TogglePushButton(FluentIcon.FILTER, "", parent)
        self._post_filter_btn.clicked.connect(self._open_post_filter_dialog)
        self._post_filter_refresh_timer = QTimer(self)
        self._post_filter_refresh_timer.setSingleShot(True)
        self._post_filter_refresh_timer.setInterval(120)
        self._post_filter_refresh_timer.timeout.connect(self._refresh_post_filtered_results)
        self._sync_post_filter_button()
        return self._post_filter_btn

    def _visible_videos(self) -> list[SearchVideo]:
        return apply_post_filter(self._all_videos, self._post_filter)

    def _post_filter_applies(self) -> bool:
        return self._post_filter.is_active() and not self._grid_only_results()

    def _sync_post_filter_button(self):
        button = getattr(self, "_post_filter_btn", None)
        if button is None:
            return
        count = self._post_filter.condition_count()
        label = tr("Filter", "筛选", "絞り込み")
        button.setText(f"{label} · {count}" if count else label)
        button.setChecked(self._post_filter.is_active())
        button.setEnabled(not self._grid_only_results())
        button.setToolTip(
            tr(
                "Filter and re-sort the loaded results by likes, views, duration and more",
                "按点赞、播放、时长等条件筛选并重新排序已加载的结果",
                "読み込み済みの結果をいいね・再生数・長さなどで絞り込み、並べ替えます",
            )
        )

    def _open_post_filter_dialog(self):
        dialog = SearchPostFilterDialog(self.window(), self._post_filter)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        if accepted and dialog.result_filter != self._post_filter:
            self._set_post_filter(dialog.result_filter)
        else:
            self._sync_post_filter_button()  # undo the toggle the click made

    def _set_post_filter(self, post_filter: PostFilter):
        self._post_filter = post_filter
        self._sync_post_filter_button()
        self._post_filter_refresh_timer.stop()
        self._render_results()
        self._start_image_loading()
        self._update_status()

    def _clear_post_filter(self):
        if self._post_filter.is_active():
            self._post_filter = PostFilter()
            self._sync_post_filter_button()

    def _displayed_video_keys(self) -> list[str]:
        if self._is_list_view():
            keys = []
            for row in range(self._results_table.rowCount()):
                item = self._results_table.item(row, 0)
                data = item.data(self._DATA_ROLE) if item else None
                if isinstance(data, dict) and data.get("kind") == "video":
                    keys.append(str(data.get("key") or ""))
            return keys
        return [key for key in self._item_by_key if key.startswith("video:")]

    def _post_filter_layout_changed(self) -> bool:
        """New statistics moved a result into, out of, or within the filtered list."""

        if not self._post_filter_applies():
            return False
        wanted = [f"video:{video.video_id}" for video in self._visible_videos()]
        return wanted != self._displayed_video_keys()

    def _schedule_post_filter_refresh(self):
        self._post_filter_refresh_timer.start()

    def _refresh_post_filtered_results(self):
        if not self._post_filter_layout_changed():
            return
        selected = set(self._selected_keys())
        self._render_results()
        self._select_keys(selected)
        self._start_image_loading()
        self._update_status()

    def _post_filter_status_suffix(self) -> str:
        if not self._post_filter_applies():
            return ""
        shown = len(self._visible_videos())
        return tr(f" · {shown} after filtering", f" · 筛选后 {shown} 个", f" · 絞り込み後 {shown} 件")
