"""Post-search filtering: conditions the Iwara API cannot take before a search."""
from __future__ import annotations

import os
import tempfile
import unittest
from contextlib import ExitStack
from datetime import date
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QSettings
from PySide6.QtWidgets import QApplication

from app.config import app_config
from app.core.search.models import SearchVideo
from app.core.search.post_filter import PostFilter, apply_post_filter
from app.i18n import tr
from app.ui.search.page import SearchInterface
from app.ui.search.post_filter_dialog import SearchPostFilterDialog

TODAY = date(2026, 11, 8)


def _video(video_id: str, *, likes=0, views=0, minutes=0.0, published="2026-11-01", tags=(), **kwargs) -> SearchVideo:
    return SearchVideo(
        video_id=video_id, title=video_id, likes=likes, views=views, duration=minutes * 60,
        published_at=published, tags=tuple(tags), **kwargs,
    )


VIDEOS = [
    _video("a", likes=50, views=1000, minutes=3, published="2026-11-07", tags=("dance",)),
    _video("b", likes=900, views=10000, minutes=12, published="2026-06-01", tags=("mmd", "song")),
    _video("c", likes=300, views=2000, minutes=1, published="2026-10-20", tags=("gore",)),
    _video("d", likes=2000, views=50000, minutes=25, published="2025-01-01"),
]


def _ids(videos):
    return [video.video_id for video in videos]


class PostFilterCoreTests(unittest.TestCase):
    def test_inactive_filter_keeps_the_server_order(self):
        self.assertFalse(PostFilter().is_active())
        self.assertEqual(_ids(apply_post_filter(VIDEOS, PostFilter(), today=TODAY)), ["a", "b", "c", "d"])

    def test_ranges_are_inclusive(self):
        result = apply_post_filter(VIDEOS, PostFilter(min_likes=300, max_likes=900), today=TODAY)
        self.assertEqual(_ids(result), ["b", "c"])
        result = apply_post_filter(VIDEOS, PostFilter(min_duration_minutes=3, max_duration_minutes=12), today=TODAY)
        self.assertEqual(_ids(result), ["a", "b"])
        result = apply_post_filter(VIDEOS, PostFilter(max_views=2000), today=TODAY)
        self.assertEqual(_ids(result), ["a", "c"])

    def test_published_within_like_ratio_and_excluded_tags(self):
        self.assertEqual(
            _ids(apply_post_filter(VIDEOS, PostFilter(published_within_days=30), today=TODAY)), ["a", "c"]
        )
        # c: 15 %, b: 9 %, a: 5 %, d: 4 %
        self.assertEqual(
            _ids(apply_post_filter(VIDEOS, PostFilter(min_like_ratio_percent=5), today=TODAY)), ["a", "b", "c"]
        )
        self.assertEqual(
            _ids(apply_post_filter(VIDEOS, PostFilter(exclude_tags=("GORE", "son")), today=TODAY)), ["a", "d"]
        )

    def test_local_sorts(self):
        def order(sort):
            return _ids(apply_post_filter(VIDEOS, PostFilter(sort=sort), today=TODAY))

        self.assertEqual(order("likes"), ["d", "b", "c", "a"])
        self.assertEqual(order("like_ratio"), ["c", "b", "a", "d"])
        self.assertEqual(order("duration_asc"), ["c", "a", "b", "d"])
        self.assertEqual(order("date_desc"), ["a", "c", "b", "d"])

    def test_unresolved_oreno3d_cards_neither_match_nor_rank_as_zero(self):
        pending = _video("oreno3d:1", source_kind="oreno3d")
        videos = [pending, *VIDEOS]
        self.assertNotIn("oreno3d:1", _ids(apply_post_filter(videos, PostFilter(max_likes=100), today=TODAY)))
        self.assertEqual(_ids(apply_post_filter(videos, PostFilter(sort="duration_asc"), today=TODAY))[-1], "oreno3d:1")
        pending.raw["_iwara_metadata_loaded"] = True
        self.assertIn("oreno3d:1", _ids(apply_post_filter(videos, PostFilter(max_likes=100), today=TODAY)))

    def test_counts_and_range_validation(self):
        value = PostFilter(min_likes=10, exclude_tags=("x",), sort="likes")
        self.assertEqual(value.condition_count(), 2)
        self.assertEqual(PostFilter(min_views=10, max_views=5).validation_error(), "views")
        self.assertEqual(PostFilter(min_views=5, max_views=10).validation_error(), "")


class PostFilterInterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.context = ExitStack()
        self.addCleanup(self.context.close)
        root = self.context.enter_context(tempfile.TemporaryDirectory())
        settings = QSettings(os.path.join(root, "config.ini"), QSettings.Format.IniFormat)
        self.context.enter_context(patch.object(app_config, "_qs", settings))
        app_config.search_auto_search_enabled = False
        manager = Mock()
        manager.history.get_records.return_value = {}
        manager.get_search_tag_suggestions.return_value = []
        self.context.enter_context(patch("app.ui.search.page.download_manager", manager))
        self.context.enter_context(patch("app.ui.rules.rule_picker.RulePicker.refresh_rules"))
        self.page = SearchInterface()
        self.addCleanup(self.close_page)
        self.context.enter_context(patch.object(self.page, "_run_search"))
        self.context.enter_context(patch.object(self.page, "_start_image_loading"))
        self.page._all_videos = [_video(v.video_id, likes=v.likes, views=v.views) for v in VIDEOS]
        self.page._render_results()

    def close_page(self):
        self.page.shutdown(timeout_ms=1000)
        self.page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def grid_ids(self):
        return [key.removeprefix("video:") for key in self.page._item_by_key]

    def test_button_sits_in_the_existing_options_row(self):
        page = self.page
        self.assertIs(page._post_filter_btn.parent(), page._query_card)
        self.assertFalse(page._post_filter_btn.isChecked())
        self.assertEqual(page._post_filter_btn.text(), tr("Filter", "筛选", "絞り込み"))

    def test_applying_a_filter_rerenders_grid_table_and_status(self):
        page = self.page
        page._set_post_filter(PostFilter(min_likes=300, sort="likes"))
        self.assertEqual(self.grid_ids(), ["d", "b", "c"])
        self.assertTrue(page._post_filter_btn.isChecked())
        self.assertEqual(page._post_filter_btn.text(), tr("Filter", "筛选", "絞り込み") + " · 1")
        self.assertIn(tr(" · 3 after filtering", " · 筛选后 3 个", " · 絞り込み後 3 件"), page._status_label.text())
        page._set_combo_data(page._view_combo, "list")
        self.assertEqual(page._results_table.rowCount(), 3)
        page._set_combo_data(page._view_combo, "grid")
        page._reset_filters()
        self.assertFalse(page._post_filter.is_active())
        self.assertFalse(page._post_filter_btn.isChecked())

    def test_resolved_statistics_bring_a_hidden_result_in(self):
        page = self.page
        page._set_post_filter(PostFilter(min_likes=1000))
        self.assertEqual(self.grid_ids(), ["d"])
        page._all_videos[0].likes = 5000
        page._update_video_presentation(page._all_videos[0])
        self.assertTrue(page._post_filter_refresh_timer.isActive())
        page._post_filter_refresh_timer.stop()
        page._refresh_post_filtered_results()
        self.assertEqual(self.grid_ids(), ["a", "d"])

    def test_dialog_round_trip_and_validation(self):
        current = PostFilter(min_likes=10, max_duration_minutes=2.5, published_within_days=7,
                             exclude_tags=("a", "b"), sort="views")
        dialog = SearchPostFilterDialog(self.page, current)
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.post_filter(), current)
        dialog._min_minutes.setText("3,5")
        self.assertEqual(dialog.post_filter().min_duration_minutes, 3.5)
        self.assertFalse(dialog.validate())  # 3.5 > 2.5
        self.assertFalse(dialog._error_label.isHidden())
        dialog._clear()
        self.assertEqual(dialog.post_filter(), PostFilter())
        self.assertTrue(dialog.validate())


if __name__ == "__main__":
    unittest.main()
