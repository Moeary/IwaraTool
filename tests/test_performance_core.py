import os
import gc
import json
import shutil
import tempfile
import unittest
from unittest.mock import call, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtWidgets import QApplication, QAbstractItemView, QTableWidget
from qfluentwidgets import Action, FluentIcon

from app.config import app_config
from app.core.history import DownloadHistory
from app.core.manager import DownloadManager, _compact_video_raw_json, _iwara_image_url, _subscription_item_from_video, download_manager
from app.core.models import DownloadTask, TaskStatus
from app.core.subscriptions import SubscriptionStore
from app.ui.download_page import DownloadInterface
from app.ui.history_page import HistoryInterface
from app.ui.search_page import SearchInterface
from app.ui.subscription_page import (
    SubscriptionInterface,
    _source_search_text,
    _source_sort_key,
    _title_matcher,
    _split_title_keywords,
    _title_matches_keywords,
)
from app.ui.task_page import TaskCenterInterface
from app.ui.ui_state import (
    apply_table_column_layout,
    connect_table_width_saver,
    restore_table_columns,
    restore_table_widths,
)
TEMP_DIRS: list[str] = []


class FakeExecutor:
    def __init__(self):
        self.submitted = []

    def submit(self, fn, *args, **kwargs):
        self.submitted.append((fn, args, kwargs))
        return None


class ConfigGuard:
    def __enter__(self):
        self.max_concurrent = app_config.max_concurrent
        self.skip_existing_files = app_config.skip_existing_files
        self.task_stall_timeout_seconds = app_config.task_stall_timeout_seconds
        self.auto_restore_stalled_cancelled = app_config.auto_restore_stalled_cancelled
        app_config.max_concurrent = 3
        app_config.skip_existing_files = False
        app_config.task_stall_timeout_seconds = 30
        app_config.auto_restore_stalled_cancelled = False
        return self

    def __exit__(self, *_exc):
        app_config.max_concurrent = self.max_concurrent
        app_config.skip_existing_files = self.skip_existing_files
        app_config.task_stall_timeout_seconds = self.task_stall_timeout_seconds
        app_config.auto_restore_stalled_cancelled = self.auto_restore_stalled_cancelled


def make_manager() -> DownloadManager:
    mgr = DownloadManager()
    tmp_dir = tempfile.mkdtemp(prefix="iwaratool-test-")
    TEMP_DIRS.append(tmp_dir)
    mgr.history = DownloadHistory(os.path.join(tmp_dir, "history.db"))
    mgr.subscriptions = SubscriptionStore(os.path.join(tmp_dir, "subscriptions.db"))
    mgr._resolve_executor = FakeExecutor()
    return mgr


class ManagerPerformanceTests(unittest.TestCase):
    def test_bulk_enqueue_5000_dedupes_and_activates_limit(self):
        with ConfigGuard():
            mgr = make_manager()
            ids = [(f"video{i:05d}", f"https://www.iwara.tv/video/video{i:05d}") for i in range(5000)]
            summary = mgr._enqueue_video_ids_bulk(ids)

            self.assertEqual(summary["queued"], 5000)
            self.assertEqual(len(mgr.get_tasks()), 5000)
            self.assertEqual(len(mgr._task_id_by_video_id), 5000)
            self.assertEqual(len(mgr._active_task_ids), 3)
            self.assertEqual(len(mgr._queued_meta_ids), 4997)
            self.assertEqual(len(mgr._resolve_executor.submitted), 3)

    def test_duplicate_enqueue_keeps_indexes_consistent(self):
        with ConfigGuard():
            mgr = make_manager()
            summary = mgr._enqueue_video_ids_bulk(
                [
                    ("sameVideo01", "https://www.iwara.tv/video/sameVideo01"),
                    ("sameVideo01", "https://www.iwara.tv/video/sameVideo01"),
                ]
            )

            self.assertEqual(summary["queued"], 1)
            self.assertEqual(summary["duplicates"], 1)
            self.assertEqual(len(mgr.get_tasks()), 1)
            self.assertEqual(list(mgr._task_id_by_video_id.keys()), ["samevideo01"])

    def test_terminal_prune_keeps_active_and_queued(self):
        mgr = make_manager()
        mgr._terminal_keep_limit = 5
        with mgr._lock:
            queued = DownloadTask("queued", "", "queued", status=TaskStatus.QUEUED_META)
            active = DownloadTask("active", "", "active", status=TaskStatus.RESOLVING)
            mgr._tasks[queued.task_id] = queued
            mgr._tasks[active.task_id] = active
            mgr._task_id_by_video_id[queued.video_id] = queued.task_id
            mgr._task_id_by_video_id[active.video_id] = active.task_id
            mgr._queued_meta_ids.append(queued.task_id)
            mgr._active_task_ids.add(active.task_id)
            for i in range(10):
                task = DownloadTask(f"done{i}", "", f"done{i}", status=TaskStatus.COMPLETED)
                task.raw_json = "x" * 10000
                mgr._tasks[task.task_id] = task
                mgr._task_id_by_video_id[task.video_id] = task.task_id
                mgr._mark_terminal_locked(task)

        removed = mgr._prune_terminal_tasks()
        statuses = {task.task_id: task.status for task in mgr.get_tasks()}

        self.assertEqual(len(removed), 5)
        self.assertIn("queued", statuses)
        self.assertIn("active", statuses)
        self.assertEqual(sum(1 for s in statuses.values() if s == TaskStatus.COMPLETED), 5)
        self.assertEqual(len(mgr._task_id_by_video_id), 7)
        self.assertEqual(len(mgr._terminal_task_id_set), 5)
        self.assertTrue(
            all(task.raw_json == "" and task.tags_json == "" for task in mgr.get_tasks() if task.status == TaskStatus.COMPLETED)
        )

    def test_terminal_prune_keeps_cancelled_tasks_for_restore(self):
        mgr = make_manager()
        mgr._terminal_keep_limit = 3
        with mgr._lock:
            for i in range(5):
                task = DownloadTask(f"cancelled{i}", "", f"cancelled{i}", status=TaskStatus.CANCELLED)
                mgr._tasks[task.task_id] = task
                mgr._task_id_by_video_id[task.video_id] = task.task_id
                mgr._mark_terminal_locked(task)
            for i in range(6):
                task = DownloadTask(f"done{i}", "", f"done{i}", status=TaskStatus.COMPLETED)
                mgr._tasks[task.task_id] = task
                mgr._task_id_by_video_id[task.video_id] = task.task_id
                mgr._mark_terminal_locked(task)

        removed = mgr._prune_terminal_tasks()
        statuses = {task.task_id: task.status for task in mgr.get_tasks()}

        self.assertEqual(len(removed), 3)
        self.assertEqual(sum(1 for s in statuses.values() if s == TaskStatus.CANCELLED), 5)
        self.assertEqual(sum(1 for s in statuses.values() if s == TaskStatus.COMPLETED), 3)

    def test_cancel_terminal_frees_slot_for_next_queued_task(self):
        with ConfigGuard():
            app_config.max_concurrent = 1
            mgr = make_manager()
            active = DownloadTask("active", "", "active", status=TaskStatus.DOWNLOADING)
            queued = DownloadTask("queued", "", "queued", status=TaskStatus.QUEUED_META)
            with mgr._lock:
                mgr._tasks[active.task_id] = active
                mgr._tasks[queued.task_id] = queued
                mgr._task_id_by_video_id[active.video_id] = active.task_id
                mgr._task_id_by_video_id[queued.video_id] = queued.task_id
                mgr._active_task_ids.add(active.task_id)
                mgr._queued_meta_ids.append(queued.task_id)

            mgr._cancel_task_terminal("active", "cancelled")

            self.assertEqual(active.status, TaskStatus.CANCELLED)
            self.assertEqual(queued.status, TaskStatus.RESOLVING)
            self.assertEqual(len(mgr._resolve_executor.submitted), 1)
            self.assertEqual(mgr._resolve_executor.submitted[0][1], ("queued",))

    def test_cancelled_task_can_be_restored_without_deleting_temp(self):
        with ConfigGuard():
            mgr = make_manager()
            tmp_dir = os.path.dirname(mgr.history._db_path)
            final_path = os.path.join(tmp_dir, "cancelled.mp4")
            temp_path = f"{final_path}_temp"
            with open(temp_path, "wb") as fh:
                fh.write(b"partial")
            task = DownloadTask(
                "cancelled",
                "https://www.iwara.tv/video/cancelled",
                "cancelled",
                title="Cancelled Video",
                status=TaskStatus.CANCELLED,
                file_path=final_path,
                downloaded_bytes=7,
                total_bytes=99,
                error_msg="cancelled by test",
                cancel_requested=True,
                delete_temp_on_cancel=True,
                remove_after_cancel=True,
            )
            with mgr._lock:
                mgr._tasks[task.task_id] = task
                mgr._task_id_by_video_id[task.video_id] = task.task_id
                mgr._mark_terminal_locked(task)

            restored = mgr.restore_cancelled_task("cancelled")

            self.assertTrue(restored)
            self.assertEqual(task.status, TaskStatus.RESOLVING)
            self.assertFalse(task.cancel_requested)
            self.assertFalse(task.delete_temp_on_cancel)
            self.assertFalse(task.remove_after_cancel)
            self.assertEqual(task.error_msg, "")
            self.assertEqual(task.cancel_origin, "")
            self.assertTrue(os.path.exists(temp_path))
            self.assertNotIn("cancelled", mgr._terminal_task_id_set)
            self.assertEqual(len(mgr._resolve_executor.submitted), 1)
            self.assertEqual(mgr._resolve_executor.submitted[0][1], ("cancelled",))

    def test_restore_all_cancelled_requeues_every_cancelled_task(self):
        with ConfigGuard():
            app_config.max_concurrent = 2
            mgr = make_manager()
            with mgr._lock:
                for i in range(4):
                    task = DownloadTask(f"cancelled{i}", "", f"cancelled{i}", status=TaskStatus.CANCELLED)
                    task.cancel_requested = True
                    task.error_msg = "cancelled by test"
                    mgr._tasks[task.task_id] = task
                    mgr._task_id_by_video_id[task.video_id] = task.task_id
                    mgr._mark_terminal_locked(task)

            restored = mgr.restore_all_cancelled()
            statuses = {task.task_id: task.status for task in mgr.get_tasks()}

            self.assertEqual(restored, 4)
            self.assertEqual(sum(1 for s in statuses.values() if s == TaskStatus.RESOLVING), 2)
            self.assertEqual(sum(1 for s in statuses.values() if s == TaskStatus.QUEUED_META), 2)
            self.assertEqual(len(mgr._resolve_executor.submitted), 2)
            self.assertFalse(any(task.cancel_requested for task in mgr.get_tasks()))

    def test_auto_restore_stalled_cancelled_only_when_idle(self):
        with ConfigGuard():
            app_config.max_concurrent = 2
            app_config.auto_restore_stalled_cancelled = True
            mgr = make_manager()
            with mgr._lock:
                stalled = DownloadTask("stalled", "", "stalled", status=TaskStatus.CANCELLED)
                stalled.cancel_origin = "auto_stall"
                manual = DownloadTask("manual", "", "manual", status=TaskStatus.CANCELLED)
                manual.cancel_origin = "manual"
                active = DownloadTask("active", "", "active", status=TaskStatus.RESOLVING)
                for task in (stalled, manual, active):
                    mgr._tasks[task.task_id] = task
                    mgr._task_id_by_video_id[task.video_id] = task.task_id
                mgr._active_task_ids.add(active.task_id)
                mgr._mark_terminal_locked(stalled)
                mgr._mark_terminal_locked(manual)

            restored_busy = mgr._restore_auto_stalled_cancelled_if_idle()
            with mgr._lock:
                active.status = TaskStatus.COMPLETED
                mgr._mark_terminal_locked(active)
            restored_idle = mgr._restore_auto_stalled_cancelled_if_idle()

            self.assertEqual(restored_busy, 0)
            self.assertEqual(restored_idle, 1)
            self.assertEqual(stalled.status, TaskStatus.RESOLVING)
            self.assertEqual(stalled.cancel_origin, "")
            self.assertEqual(manual.status, TaskStatus.CANCELLED)
            self.assertEqual(len(mgr._resolve_executor.submitted), 1)

    def test_stall_watchdog_auto_cancels_stale_active_and_frees_slot(self):
        with ConfigGuard():
            app_config.max_concurrent = 1
            app_config.task_stall_timeout_seconds = 1
            mgr = make_manager()
            active = DownloadTask("active", "", "active", status=TaskStatus.RESOLVING)
            queued = DownloadTask("queued", "", "queued", status=TaskStatus.QUEUED_META)
            with mgr._lock:
                mgr._tasks[active.task_id] = active
                mgr._tasks[queued.task_id] = queued
                mgr._task_id_by_video_id[active.video_id] = active.task_id
                mgr._task_id_by_video_id[queued.video_id] = queued.task_id
                mgr._active_task_ids.add(active.task_id)
                mgr._queued_meta_ids.append(queued.task_id)
                mgr._task_last_activity[active.task_id] = 0

            cancelled = mgr._cancel_stale_tasks()

            self.assertEqual(cancelled, 1)
            self.assertEqual(active.status, TaskStatus.CANCELLED)
            self.assertTrue(active.cancel_requested)
            self.assertEqual(active.cancel_origin, "auto_stall")
            self.assertEqual(queued.status, TaskStatus.RESOLVING)
            self.assertEqual(len(mgr._resolve_executor.submitted), 1)

    def test_clear_completed_keeps_failed_and_cancelled_tasks(self):
        mgr = make_manager()
        with mgr._lock:
            for status in (
                TaskStatus.COMPLETED,
                TaskStatus.SKIPPED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
            ):
                task_id = status.value
                task = DownloadTask(task_id, "", task_id, status=status)
                mgr._tasks[task_id] = task
                mgr._task_id_by_video_id[task.video_id] = task.task_id
                mgr._mark_terminal_locked(task)

        mgr.clear_completed()
        statuses = {task.task_id: task.status for task in mgr.get_tasks()}

        self.assertNotIn(TaskStatus.COMPLETED.value, statuses)
        self.assertNotIn(TaskStatus.SKIPPED.value, statuses)
        self.assertEqual(statuses[TaskStatus.FAILED.value], TaskStatus.FAILED)
        self.assertEqual(statuses[TaskStatus.CANCELLED.value], TaskStatus.CANCELLED)

    def test_subscription_items_use_current_task_status(self):
        mgr = make_manager()
        source_id = mgr.subscriptions.add_source("author", "alice", "Alice")
        mgr.subscriptions.upsert_items(
            source_id,
            [
                {
                    "video_id": "videoQueued01",
                    "title": "Queued",
                    "author": "alice",
                    "source_url": "https://www.iwara.tv/video/videoQueued01",
                }
            ],
        )
        with mgr._lock:
            task = DownloadTask(
                "task-cancelled",
                "",
                "videoQueued01",
                status=TaskStatus.CANCELLED,
            )
            mgr._tasks[task.task_id] = task
            mgr._task_id_by_video_id[task.video_id.lower()] = task.task_id

        items = mgr.get_subscription_items(source_id)

        self.assertEqual(items[0]["task_status"], TaskStatus.CANCELLED.value)
        self.assertTrue(items[0]["queued"])

        mgr.remove_task("task-cancelled")
        items = mgr.get_subscription_items(source_id)

        self.assertEqual(items[0]["task_status"], "")
        self.assertFalse(items[0]["queued"])

    def test_history_list_and_batch_queries_omit_heavy_fields_by_default(self):
        tmp_dir = tempfile.mkdtemp(prefix="iwaratool-history-")
        TEMP_DIRS.append(tmp_dir)
        history = DownloadHistory(os.path.join(tmp_dir, "history.db"))
        history.upsert_downloaded(
            {
                "video_id": "heavy01",
                "title": "Heavy",
                "tags_json": json.dumps([{"name": "tag"}]),
                "raw_json": json.dumps({"body": "x" * 10000}),
                "file_path": os.path.join(tmp_dir, "heavy01.mp4"),
            }
        )

        listed = history.list_records()[0]
        batched = history.get_records(["heavy01"])["heavy01"]
        full = history.get_record("heavy01", include_raw=True)

        self.assertNotIn("raw_json", listed)
        self.assertNotIn("tags_json", listed)
        self.assertNotIn("raw_json", batched)
        self.assertNotIn("tags_json", batched)
        self.assertIn("raw_json", full)
        self.assertIn("tags_json", full)

    def test_history_remove_many_deletes_only_requested_records(self):
        tmp_dir = tempfile.mkdtemp(prefix="iwaratool-history-")
        TEMP_DIRS.append(tmp_dir)
        history = DownloadHistory(os.path.join(tmp_dir, "history.db"))
        for video_id in ("remove01", "remove02", "keep01"):
            history.upsert_downloaded({"video_id": video_id, "title": video_id})

        removed = history.remove_many(["remove01", "missing", "remove02"])

        self.assertEqual(removed, 2)
        self.assertEqual([row["video_id"] for row in history.list_records()], ["keep01"])

    def test_compact_raw_json_keeps_nfo_fields_without_full_payload(self):
        raw = _compact_video_raw_json(
            {
                "id": "video01",
                "title": "Title",
                "body": "video body",
                "download": {"urls": ["x" * 10000]},
                "file": {"huge": "y" * 10000},
                "user": {
                    "username": "author",
                    "profile": {"description": "author profile"},
                },
            }
        )
        data = json.loads(raw)

        self.assertEqual(data["body"], "video body")
        self.assertEqual(data["user"]["profile"]["description"], "author profile")
        self.assertNotIn("download", data)
        self.assertNotIn("file", data)
        self.assertLess(len(raw), 1000)

    def test_mark_subscription_items_downloaded_creates_moved_history(self):
        mgr = make_manager()
        source_id = mgr.subscriptions.add_source("author", "author01", "Author 01")
        mgr.subscriptions.upsert_items(
            source_id,
            [
                {
                    "video_id": "videoMoved01",
                    "title": "Moved Video",
                    "author": "author01",
                    "published_at": "2026-06-12T00:00:00Z",
                    "source_url": "https://www.iwara.tv/video/videoMoved01",
                }
            ],
        )

        marked = mgr.mark_subscription_items_downloaded(["videoMoved01"])
        record = mgr.history.get_record("videoMoved01")
        item = mgr.get_subscription_items(source_id)[0]

        self.assertEqual(marked, 1)
        self.assertEqual(record["title"], "Moved Video")
        self.assertEqual(record["file_path"], "")
        self.assertTrue(item["downloaded"])
        self.assertFalse(item["download_file_exists"])

        restored = mgr.restore_subscription_items_downloaded(["videoMoved01"])
        restored_item = mgr.get_subscription_items(source_id)[0]

        self.assertEqual(restored, 1)
        self.assertIsNone(mgr.history.get_record("videoMoved01"))
        self.assertFalse(restored_item["downloaded"])

    def test_subscription_sources_include_downloaded_and_undownloaded_counts(self):
        mgr = make_manager()
        source_id = mgr.subscriptions.add_source("author", "author01", "Author 01")
        mgr.subscriptions.upsert_items(
            source_id,
            [
                {
                    "video_id": "downloaded01",
                    "title": "Downloaded",
                    "author": "author01",
                    "published_at": "2026-06-12T00:00:00Z",
                    "source_url": "https://www.iwara.tv/video/downloaded01",
                },
                {
                    "video_id": "missing01",
                    "title": "Missing",
                    "author": "author01",
                    "published_at": "2026-06-12T00:00:00Z",
                    "source_url": "https://www.iwara.tv/video/missing01",
                },
                {
                    "video_id": "unavailable01",
                    "title": "Unavailable",
                    "author": "author01",
                    "published_at": "2026-06-12T00:00:00Z",
                    "source_url": "https://www.iwara.tv/video/unavailable01",
                    "download_state": "unavailable",
                    "download_reason": "当前账号没有权限查看或下载该作品。",
                    "download_state_known": True,
                },
            ],
        )
        mgr.mark_subscription_items_downloaded(["downloaded01"])

        source = mgr.get_subscription_sources()[0]

        self.assertEqual(source["item_count"], 3)
        self.assertEqual(source["downloaded_count"], 1)
        self.assertEqual(source["undownloaded_count"], 1)
        self.assertEqual(source["unavailable_count"], 1)

    def test_subscription_item_from_external_embed_is_not_downloadable(self):
        item = _subscription_item_from_video(
            {
                "id": "embed01",
                "title": "Embed",
                "createdAt": "2026-06-12T00:00:00Z",
                "embedUrl": "https://youtu.be/example",
                "user": {"username": "author01"},
            }
        )

        self.assertEqual(item["download_state"], "unavailable")
        self.assertIn("YouTube", item["download_reason"])

    def test_subscription_refresh_marks_private_video_unavailable_from_details(self):
        mgr = make_manager()
        source_id = mgr.subscriptions.add_source("author", "author01", "Author 01")

        def fake_api_call(method_name, *args, **_kwargs):
            if method_name == "get_user_id":
                return "user01", ""
            if method_name == "get_user_videos":
                return [
                    {
                        "id": "privateRefresh01",
                        "title": "Private Refresh",
                        "createdAt": "2026-06-12T00:00:00Z",
                        "user": {"username": "author01"},
                    }
                ]
            if method_name == "get_video_info":
                return (
                    {
                        "id": args[0],
                        "title": "Private Refresh",
                        "message": "errors.privateVideo",
                        "private": True,
                        "user": {"username": "author01"},
                    },
                    "",
                )
            raise AssertionError(f"unexpected api call: {method_name}")

        mgr._api_call = fake_api_call

        summary = mgr.refresh_subscription_source(source_id)
        item = mgr.get_subscription_items(source_id)[0]

        self.assertEqual(summary["unavailable"], 1)
        self.assertEqual(summary["unavailable_checked"], 1)
        self.assertEqual(item["download_state"], "unavailable")
        self.assertTrue(
            any(
                marker in item["download_reason"]
                for marker in ("Private", "私有", "非公開")
            )
        )

    def test_manual_author_refresh_updates_display_name_from_profile(self):
        mgr = make_manager()
        source_id = mgr.subscriptions.add_source("author", "author01", "author01")

        def fake_api_call(method_name, *args, **_kwargs):
            if method_name == "get_user_profile":
                return (
                    {
                        "user": {
                            "id": "user01",
                            "username": "author01",
                            "name": "作者显示名",
                            "avatar": {"id": "avatar01", "name": "avatar01.jpg"},
                        }
                    },
                    "",
                )
            if method_name == "get_user_videos":
                return []
            raise AssertionError(f"unexpected api call: {method_name}")

        mgr._api_call = fake_api_call
        summary = mgr.refresh_subscription_source(source_id)
        source = mgr.subscriptions.get_source(source_id)

        self.assertEqual(summary["title"], "作者显示名")
        self.assertEqual(source["title"], "作者显示名")
        self.assertEqual(source["remote_id"], "user01")
        self.assertTrue(source["avatar_url"])

    def test_author_refresh_backfills_missing_avatar_for_existing_source(self):
        mgr = make_manager()
        source_id = mgr.subscriptions.add_source("author", "author01", "已有显示名", "user01")

        def fake_api_call(method_name, *args, **_kwargs):
            if method_name == "get_user_profile":
                return (
                    {
                        "user": {
                            "id": "user01",
                            "username": "author01",
                            "name": "远端显示名",
                            "avatar": {"id": "avatar01", "name": "avatar01.jpg"},
                        }
                    },
                    "",
                )
            if method_name == "get_user_videos":
                return []
            raise AssertionError(f"unexpected api call: {method_name}")

        mgr._api_call = fake_api_call
        mgr.refresh_subscription_source(source_id)
        source = mgr.subscriptions.get_source(source_id)

        self.assertEqual(source["title"], "远端显示名")
        self.assertTrue(source["avatar_url"])

    def test_subscription_refresh_reports_each_enabled_source(self):
        mgr = make_manager()
        mgr.subscriptions.add_source("feed", "feed01", "Feed 01")
        mgr.subscriptions.add_source("feed", "feed02", "Feed 02")
        disabled_id = mgr.subscriptions.add_source("feed", "feed03", "Feed 03")
        mgr.subscriptions.set_source_enabled(disabled_id, False)

        def fake_api_call(method_name, *args, **_kwargs):
            self.assertEqual(method_name, "get_subscribed_videos")
            return [], ""

        mgr._api_call = fake_api_call
        progress: list[dict] = []
        summary = mgr.refresh_all_subscriptions(progress.append)

        self.assertEqual(summary["sources"], 2)
        self.assertEqual(
            [(event["stage"], event["index"], event["total"]) for event in progress],
            [("started", 1, 2), ("finished", 1, 2), ("started", 2, 2), ("finished", 2, 2)],
        )

    def test_subscription_submit_skips_unavailable_items(self):
        old_download_video = app_config.download_video_file
        old_mark_submitted = app_config.mark_submitted_as_downloaded
        try:
            app_config.download_video_file = True
            app_config.mark_submitted_as_downloaded = False
            mgr = make_manager()
            source_id = mgr.subscriptions.add_source("author", "author01", "Author 01")
            mgr.subscriptions.upsert_items(
                source_id,
                [
                    {
                        "video_id": "private01",
                        "title": "Private",
                        "author": "author01",
                        "published_at": "2026-06-12T00:00:00Z",
                        "source_url": "https://www.iwara.tv/video/private01",
                        "download_state": "unavailable",
                        "download_reason": "当前账号没有权限查看或下载该作品。",
                        "download_state_known": True,
                    }
                ],
            )

            result = mgr.submit_subscription_items(["private01"])

            self.assertEqual(result["mode"], "empty")
            self.assertEqual(result["queued"], 0)
            self.assertEqual(result["skipped_unavailable"], 1)
            self.assertEqual(len(mgr.get_tasks()), 0)
        finally:
            app_config.download_video_file = old_download_video
            app_config.mark_submitted_as_downloaded = old_mark_submitted

    def test_subscription_store_persists_source_avatar_fields(self):
        mgr = make_manager()
        source_id = mgr.subscriptions.add_source(
            "author",
            "author01",
            "Author 01",
            "remote01",
            avatar_url="https://i.iwara.tv/image/thumbnail/avatar01/avatar01.jpeg",
        )
        mgr.subscriptions.update_source_avatar(
            source_id,
            "https://i.iwara.tv/image/thumbnail/avatar02/avatar02.jpeg",
            os.path.join("data", "img", "avatar02.jpeg"),
        )

        source = mgr.subscriptions.list_sources()[0]

        self.assertEqual(source["avatar_url"], "https://i.iwara.tv/image/thumbnail/avatar02/avatar02.jpeg")
        self.assertTrue(source["avatar_path"].endswith(os.path.join("data", "img", "avatar02.jpeg")))

    def test_iwara_image_url_uses_image_id_and_name(self):
        url = _iwara_image_url(
            {
                "id": "2f22ed06-9907-4e95-bddb-f2e81ff0116a",
                "path": "2026/03/03",
                "name": "2f22ed06-9907-4e95-bddb-f2e81ff0116a.jpeg",
            },
            variant="thumbnail",
        )

        self.assertEqual(
            url,
            "https://i.iwara.tv/image/thumbnail/2f22ed06-9907-4e95-bddb-f2e81ff0116a/2f22ed06-9907-4e95-bddb-f2e81ff0116a.jpeg",
        )

    def test_subscription_item_includes_thumbnail_url(self):
        item = _subscription_item_from_video(
            {
                "id": "video-cover-01",
                "title": "Cover Video",
                "fileUrl": "https://cdn.example.test/file",
                "file": {"id": "file-cover-01"},
                "thumbnail": 3,
            }
        )

        self.assertEqual(
            item["thumbnail_url"],
            "https://cdn.example.test/image/original/file-cover-01/thumbnail-03.jpg",
        )

    def test_cache_subscription_thumbnail_resolves_missing_url_from_details(self):
        mgr = make_manager()
        source_id = mgr.subscriptions.add_source("author", "cover-author", "Cover Author")
        mgr.subscriptions.upsert_items(
            source_id,
            [{"video_id": "cover-missing-01", "title": "Missing Cover URL"}],
        )
        detail = {
            "id": "cover-missing-01",
            "fileUrl": "https://files.iwara.tv/file/file-cover-01?expires=1",
            "file": {"id": "file-cover-01"},
            "thumbnail": 2,
        }
        with patch.object(mgr, "_api_call", return_value=(detail, "")):
            with patch.object(
                mgr.subscription_image_cache,
                "get_or_fetch",
                return_value="D:/cache/sub/video-cover.jpg",
            ) as fetch:
                path = mgr.cache_subscription_thumbnail("cover-missing-01", "")

        self.assertEqual(path, "D:/cache/sub/video-cover.jpg")
        fetch.assert_called_once()
        stored = mgr.subscriptions.list_items(source_id)[0]
        self.assertIn("thumbnail-02.jpg", stored["thumbnail_url"])

    def test_subscription_store_remove_source_deletes_items_only(self):
        mgr = make_manager()
        source_id = mgr.subscriptions.add_source("author", "dead-author", "")
        mgr.subscriptions.upsert_items(
            source_id,
            [{"video_id": "dead-video", "title": "Cached video"}],
        )

        mgr.remove_subscription_source(source_id)

        self.assertEqual(mgr.subscriptions.list_sources(), [])
        self.assertEqual(mgr.subscriptions.list_items(), [])

    def test_subscription_store_remove_sources_supports_batch_deletion(self):
        mgr = make_manager()
        source_ids = [
            mgr.subscriptions.add_source("feed", f"batch-feed-{index}", f"Feed {index}")
            for index in range(3)
        ]
        for index, source_id in enumerate(source_ids):
            mgr.subscriptions.upsert_items(
                source_id,
                [{"video_id": f"batch-video-{index}", "title": "Cached video"}],
            )

        removed = mgr.remove_subscription_sources(source_ids[:2])

        self.assertEqual(removed, 2)
        self.assertEqual(
            [source["id"] for source in mgr.subscriptions.list_sources()],
            [source_ids[2]],
        )
        self.assertEqual(
            [item["video_id"] for item in mgr.subscriptions.list_items()],
            ["batch-video-2"],
        )

    def test_subscription_store_migrates_legacy_db_into_history_db(self):
        tmp_dir = tempfile.mkdtemp(prefix="iwaratool-subscription-migrate-")
        TEMP_DIRS.append(tmp_dir)
        legacy_path = os.path.join(tmp_dir, "subscriptions.db")
        history_path = os.path.join(tmp_dir, "history.db")

        legacy_store = SubscriptionStore(legacy_path, legacy_db_path="")
        legacy_source_id = legacy_store.add_source("author", "author01", "Author 01")
        legacy_store.upsert_items(
            legacy_source_id,
            [
                {
                    "video_id": "legacyVideo01",
                    "title": "Legacy Video",
                    "author": "author01",
                    "published_at": "2026-06-12T00:00:00Z",
                    "source_url": "https://www.iwara.tv/video/legacyVideo01",
                }
            ],
        )
        del legacy_store
        gc.collect()

        merged_store = SubscriptionStore(history_path, legacy_db_path=legacy_path)
        sources = merged_store.list_sources()
        items = merged_store.list_items(int(sources[0]["id"]))

        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["source_key"], "author01")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["video_id"], "legacyVideo01")
        self.assertFalse(os.path.exists(legacy_path))
        self.assertTrue(any(name.startswith("subscriptions.db.migrated") for name in os.listdir(tmp_dir)))

    def test_subscription_submit_honors_metadata_only_options(self):
        mgr = make_manager()
        tmp_dir = tempfile.mkdtemp(prefix="iwaratool-subscription-metadata-")
        TEMP_DIRS.append(tmp_dir)
        source_id = mgr.subscriptions.add_source("author", "author01", "Author 01")
        mgr.subscriptions.upsert_items(
            source_id,
            [
                {
                    "video_id": "subMeta01",
                    "title": "Cached Title",
                    "author": "author01",
                    "published_at": "2026-06-12T00:00:00Z",
                    "source_url": "https://www.iwara.tv/video/subMeta01",
                }
            ],
        )

        old_download_dir = app_config.download_dir
        old_download_video = app_config.download_video_file
        old_download_thumbnail = app_config.download_thumbnail
        old_collect_nfo = app_config.collect_nfo_info
        old_mark_submitted = app_config.mark_submitted_as_downloaded

        def fake_api_call(method_name, *args, **_kwargs):
            if method_name == "get_video_info":
                return (
                    {
                        "id": args[0],
                        "title": "Metadata Only",
                        "createdAt": "2026-06-12T00:00:00Z",
                        "numLikes": 1,
                        "numViews": 2,
                        "numComments": 3,
                        "slug": "metadata-only",
                        "rating": "general",
                        "body": "description",
                        "user": {"username": "author01"},
                        "file": {"id": "file01", "duration": 120},
                        "fileUrl": "https://files.example.test/video.mp4",
                        "thumbnail": 0,
                        "tags": [{"name": "tag01"}],
                    },
                    "",
                )
            raise AssertionError(f"unexpected api call: {method_name}")

        try:
            app_config.download_dir = tmp_dir
            app_config.download_video_file = False
            app_config.download_thumbnail = False
            app_config.collect_nfo_info = True
            app_config.mark_submitted_as_downloaded = True
            mgr._api_call = fake_api_call

            result = mgr.submit_subscription_items(["subMeta01"])
            record = mgr.history.get_record("subMeta01")
            nfo_files = [
                os.path.join(dirpath, name)
                for dirpath, _, filenames in os.walk(tmp_dir)
                for name in filenames
                if name.endswith(".nfo")
            ]

            self.assertEqual(result["mode"], "metadata")
            self.assertEqual(result["queued"], 0)
            self.assertEqual(result["marked"], 1)
            self.assertEqual(result["nfo"], 1)
            self.assertEqual(len(mgr.get_tasks()), 0)
            self.assertIsNotNone(record)
            self.assertEqual(record["file_path"], "")
            self.assertEqual(record["quality"], "")
            self.assertEqual(record["title"], "Metadata Only")
            self.assertEqual(len(nfo_files), 1)
        finally:
            app_config.download_dir = old_download_dir
            app_config.download_video_file = old_download_video
            app_config.download_thumbnail = old_download_thumbnail
            app_config.collect_nfo_info = old_collect_nfo
            app_config.mark_submitted_as_downloaded = old_mark_submitted


class UiPerformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_log_widget_keeps_max_blocks_and_flushes_in_batches(self):
        page = DownloadInterface()
        for i in range(page._MAX_LOG_BLOCKS + 250):
            page._append_log(f"log {i}")
        while page._pending_logs:
            page._flush_logs()

        self.assertLessEqual(page._log_edit.blockCount(), page._MAX_LOG_BLOCKS)

    def test_task_progress_updates_existing_row_only(self):
        with download_manager._lock:
            download_manager._tasks.clear()
            download_manager._task_id_by_video_id.clear()
            download_manager._queued_meta_ids.clear()
            download_manager._active_task_ids.clear()
            download_manager._terminal_task_ids.clear()
            download_manager._terminal_task_id_set.clear()
            task = DownloadTask(
                task_id="active",
                url="",
                video_id="active",
                title="Active",
                status=TaskStatus.DOWNLOADING,
                total_bytes=100,
                downloaded_bytes=10,
            )
            download_manager._tasks[task.task_id] = task
            download_manager._task_id_by_video_id[task.video_id] = task.task_id

        page = TaskCenterInterface()
        self.assertEqual(page._table.rowCount(), 1)
        page._on_task_progress("active", 50, 100, "1 MB/s")
        page._flush_progress_updates()

        self.assertEqual(page._table.rowCount(), 1)
        self.assertEqual(page._table.item(0, page._COL_PROGRESS).text(), "50.0%")

    def test_task_batch_add_inserts_rows_without_full_refresh(self):
        with download_manager._lock:
            download_manager._tasks.clear()
            download_manager._task_id_by_video_id.clear()
            download_manager._queued_meta_ids.clear()
            download_manager._active_task_ids.clear()
            download_manager._terminal_task_ids.clear()
            download_manager._terminal_task_id_set.clear()

        page = TaskCenterInterface()
        infos = []
        with download_manager._lock:
            for i in range(50):
                task_id = f"task{i}"
                video_id = f"video{i}"
                task = DownloadTask(task_id=task_id, url="", video_id=video_id, title=f"Video {i}")
                download_manager._tasks[task_id] = task
                download_manager._task_id_by_video_id[video_id] = task_id
                infos.append(
                    {
                        "task_id": task_id,
                        "video_id": video_id,
                        "title": task.title,
                        "author": "",
                        "status": TaskStatus.QUEUED_META.value,
                    }
                )

        page._on_tasks_added(infos)

        self.assertEqual(page._table.rowCount(), 50)
        self.assertFalse(page._refresh_pending)
        self.assertEqual(len(page._row_by_task_id), 50)

    def test_task_table_supports_extended_selection_and_bulk_status_actions(self):
        page = TaskCenterInterface()
        self.assertEqual(
            page._table.selectionMode(),
            QAbstractItemView.SelectionMode.ExtendedSelection,
        )

        tasks = {
            "failed": DownloadTask(
                task_id="failed",
                url="",
                video_id="failed-video",
                title="Failed",
                status=TaskStatus.FAILED,
            ),
            "cancelled": DownloadTask(
                task_id="cancelled",
                url="",
                video_id="cancelled-video",
                title="Cancelled",
                status=TaskStatus.CANCELLED,
            ),
            "active": DownloadTask(
                task_id="active",
                url="",
                video_id="active-video",
                title="Active",
                status=TaskStatus.DOWNLOADING,
            ),
        }
        page._tasks_by_id = tasks
        page._visible_task_ids = list(tasks)
        page._schedule_refresh = lambda *_args: None

        with patch("app.ui.task_page.download_manager") as manager, patch(
            "app.ui.task_page.InfoBar.info"
        ):
            manager.retry_task.return_value = True
            manager.restore_cancelled_task.return_value = True
            manager.cancel_task.return_value = True

            page._retry_task_ids(("failed", "active"))
            page._restore_task_ids(("cancelled", "failed"))
            page._cancel_task_ids(("active", "cancelled"))
            page._remove_task_ids(tuple(tasks))

        manager.retry_task.assert_called_once_with("failed")
        manager.restore_cancelled_task.assert_called_once_with("cancelled")
        manager.cancel_task.assert_called_once_with("active")
        manager.remove_task.assert_has_calls(
            [
                call("failed"),
                call("cancelled"),
                call("active"),
            ]
        )
        self.assertEqual(manager.remove_task.call_count, 3)

    def test_task_menu_callback_keeps_selected_ids_when_qaction_emits_checked(self):
        selected_ids = ("failed", "active")
        captured = []
        action = Action(
            FluentIcon.DELETE,
            "Remove selected tasks",
            triggered=lambda _checked=False, ids=selected_ids: captured.append(ids),
        )
        action.trigger()
        self.assertEqual(captured, [selected_ids])

    def test_table_width_saver_records_resize_immediately(self):
        key = f"test_table_widths_{id(self)}"
        table = QTableWidget()
        table.setColumnCount(3)
        restore_table_widths(table, key, {0: 50, 1: 60, 2: 70})
        connect_table_width_saver(table, key)

        table.setColumnWidth(1, 234)
        self.app.processEvents()
        raw = str(app_config.get_ui_value(key, "") or "")

        self.assertEqual(raw.split(","), [str(table.columnWidth(i)) for i in range(3)])

    def test_table_column_layout_persists_order_and_visibility(self):
        key = f"test_table_columns_{id(self)}"
        table = QTableWidget()
        table.setColumnCount(4)
        table.setHorizontalHeaderLabels(["A", "B", "C", "D"])

        restore_table_columns(table, key, default_visible=[0, 1, 2, 3])
        apply_table_column_layout(table, key, order=[2, 0, 3, 1], visible=[2, 3], sync=True)

        header = table.horizontalHeader()
        self.assertEqual([header.logicalIndex(i) for i in range(4)], [2, 0, 3, 1])
        self.assertFalse(table.isColumnHidden(2))
        self.assertFalse(table.isColumnHidden(3))
        self.assertTrue(table.isColumnHidden(0))
        self.assertTrue(table.isColumnHidden(1))

        restored = QTableWidget()
        restored.setColumnCount(4)
        restored.setHorizontalHeaderLabels(["A", "B", "C", "D"])
        restore_table_columns(restored, key, default_visible=[0, 1, 2, 3])
        restored_header = restored.horizontalHeader()

        self.assertEqual([restored_header.logicalIndex(i) for i in range(4)], [2, 0, 3, 1])
        self.assertFalse(restored.isColumnHidden(2))
        self.assertFalse(restored.isColumnHidden(3))
        self.assertTrue(restored.isColumnHidden(0))
        self.assertTrue(restored.isColumnHidden(1))

    def test_subscription_title_keyword_filter_supports_include_and_exclude(self):
        include_terms = _split_title_keywords(" MMD; dance, mmd\n")
        exclude_terms = _split_title_keywords("fixed,  test")

        self.assertEqual(include_terms, ["mmd", "dance"])
        self.assertEqual(exclude_terms, ["fixed", "test"])
        self.assertTrue(_title_matches_keywords("MMD Dance", include_terms, exclude_terms))
        self.assertFalse(_title_matches_keywords("MMD fixed camera", include_terms, exclude_terms))
        self.assertFalse(_title_matches_keywords("Unrelated", include_terms, exclude_terms))

    def test_subscription_title_search_supports_simple_and_regex_modes(self):
        self.assertTrue(_title_matcher("01-99", regex_mode=False)("R18MMD 01-99"))
        self.assertFalse(_title_matcher("01-99", regex_mode=False)("R18MMD 拆分"))
        self.assertTrue(_title_matcher(r"\b\d{2}-\d{2}\b", regex_mode=True)("R18MMD 01-99"))

    def test_subscription_pending_items_survive_title_filter_changes(self):
        mgr = make_manager()
        source_id = mgr.subscriptions.add_source("feed", "feed01", "Feed 01")
        mgr.subscriptions.upsert_items(
            source_id,
            [
                {"video_id": "pending01", "title": "Alpha Target", "source_url": "https://example.test/1"},
                {"video_id": "other01", "title": "Beta Other", "source_url": "https://example.test/2"},
            ],
        )

        with patch("app.ui.subscription_page.download_manager", mgr):
            page = SubscriptionInterface()
            page.resize(1500, 900)
            page.show()
            self.app.processEvents()
            page._add_pending_video_ids(["pending01"])
            pending_row = next(
                row
                for row in range(page._item_table.rowCount())
                if str(page._item_table.item(row, page._ITEM_ID).text()) == "pending01"
            )
            pending_background = page._item_table.item(pending_row, page._ITEM_TITLE).background()
            self.assertEqual(pending_background.style(), Qt.BrushStyle.SolidPattern)
            self.assertGreater(pending_background.color().green(), pending_background.color().red())
            self.assertGreater(pending_background.color().green(), pending_background.color().blue())

            page._remove_pending_video_ids(["pending01"])
            self.assertEqual(
                page._item_table.item(pending_row, page._ITEM_TITLE).background().style(),
                Qt.BrushStyle.NoBrush,
            )
            page._add_pending_video_ids(["pending01"])
            with patch("app.ui.subscription_page.isDarkTheme", return_value=True):
                page.refresh_theme_styles()
            self.assertEqual(
                page._item_table.item(pending_row, page._ITEM_TITLE).background().color().name(),
                "#1f5f3d",
            )
            with patch("app.ui.subscription_page.isDarkTheme", return_value=False):
                page.refresh_theme_styles()
            self.assertEqual(
                page._item_table.item(pending_row, page._ITEM_TITLE).background().color().name(),
                "#c6efce",
            )
            page._title_search_edit.setText("Beta")
            self.app.processEvents()

            self.assertEqual([item["video_id"] for item in page._visible_items], ["other01"])
            self.assertEqual(page._operation_video_ids(), ["pending01"])

            page._title_filter_mode_btn.setChecked(True)
            page._title_search_edit.setText(r"^Alpha")
            self.app.processEvents()

            self.assertEqual([item["video_id"] for item in page._visible_items], ["pending01"])
            self.assertEqual(page._operation_video_ids(), ["pending01"])
            with patch.object(page, "_apply_selected_rule_for_download") as apply_rule:
                with patch.object(page, "_enqueue_ids") as enqueue_ids:
                    # QAction.triggered emits a bool; the context-menu path
                    # passes an explicit ID list and must not be overridden by
                    # the staged pending selection.
                    page._download_selected_with_rule(False)
                    page._download_selected_with_rule(["other01"])
                    self.assertEqual(
                        enqueue_ids.call_args_list,
                        [call(["pending01"]), call(["other01"])],
                    )
                    self.assertEqual(apply_rule.call_count, 2)
        page.close()

    def test_subscription_source_table_supports_extended_selection_and_batch_actions(self):
        mgr = make_manager()
        source_ids = [
            mgr.subscriptions.add_source("feed", f"select-feed-{index}", f"Feed {index}")
            for index in range(3)
        ]

        with patch("app.ui.subscription_page.download_manager", mgr):
            page = SubscriptionInterface()
            page.resize(800, 900)
            page.show()
            self.app.processEvents()
            try:
                self.assertEqual(
                    page._source_table.selectionMode(),
                    QAbstractItemView.SelectionMode.ExtendedSelection,
                )
                self.assertTrue(page._splitter_is_vertical)

                page._source_table.selectRow(0)
                page._source_table.selectionModel().select(
                    page._source_table.model().index(1, 0),
                    QItemSelectionModel.SelectionFlag.Select
                    | QItemSelectionModel.SelectionFlag.Rows,
                )
                expected_ids = [
                    int(page._sources[row]["id"])
                    for row in (0, 1)
                ]
                selected = page._selected_source_ids()
                self.assertEqual(selected, expected_ids)

                with patch.object(page, "_start_refresh") as start_refresh:
                    page._refresh_selected_sources()
                    start_refresh.assert_called_once_with(
                        expected_ids,
                        ignore_disabled=True,
                    )
            finally:
                page.close()

    def test_history_table_supports_extended_selection(self):
        records = [
            {"video_id": "history01", "title": "History 01", "file_path": ""},
            {"video_id": "history02", "title": "History 02", "file_path": ""},
            {"video_id": "history03", "title": "History 03", "file_path": ""},
        ]
        with patch("app.ui.history_page.download_manager") as manager:
            manager.get_history_records.return_value = records
            page = HistoryInterface()
            self.assertEqual(
                page._table.selectionMode(),
                QAbstractItemView.SelectionMode.ExtendedSelection,
            )

            page._table.selectRow(0)
            page._table.selectionModel().select(
                page._table.model().index(1, 0),
                QItemSelectionModel.SelectionFlag.Select
                | QItemSelectionModel.SelectionFlag.Rows,
            )

            self.assertEqual(page._selected_video_ids(), ["history01", "history02"])
            page.close()

    def test_search_page_controls_and_history_popup_survive_navigation(self):
        old_auto_search = app_config.search_auto_search_enabled
        old_collapsed = app_config.get_ui_value("search_controls_collapsed_v1", False)
        page = None
        try:
            app_config.search_auto_search_enabled = True
            page = SearchInterface()
            self.assertFalse(hasattr(page, "_update_tags_btn"))
            page._set_search_controls_collapsed(False, persist=False)

            with patch.object(page, "_start_search") as start_search:
                page._schedule_auto_search()
                self.assertTrue(page._auto_search_timer.isActive())
                page._auto_start_search()
                start_search.assert_called_once_with()

            page._set_search_controls_collapsed(True, persist=False)
            self.assertTrue(page._query_card.isHidden())
            self.assertTrue(page._rule_card.isHidden())
            page._set_search_controls_collapsed(False, persist=False)
            self.assertFalse(page._query_card.isHidden())
            self.assertFalse(page._rule_card.isHidden())

            page.show()
            self.app.processEvents()
            page._search_history_popup.show()
            page.hide()
            self.app.processEvents()
            self.assertTrue(page._search_history_popup.isHidden())
            page.show()
            self.app.processEvents()
            self.assertTrue(page._search_history_popup.isHidden())
        finally:
            if page is not None:
                page.close()
            app_config.search_auto_search_enabled = old_auto_search
            app_config.set_ui_value("search_controls_collapsed_v1", old_collapsed)

    def test_title_rule_filters_download_metadata(self):
        old_include = app_config.filter_title_include
        old_exclude = app_config.filter_title_exclude
        try:
            app_config.filter_title_include = "01-99"
            app_config.filter_title_exclude = "拆分"
            mgr = make_manager()

            self.assertTrue(
                mgr._passes_filters(
                    title="R18MMD 01-99",
                    likes=0,
                    views=0,
                    published_at="",
                    tags=[],
                )[0]
            )
            self.assertFalse(
                mgr._passes_filters(
                    title="R18MMD 拆分 01-99",
                    likes=0,
                    views=0,
                    published_at="",
                    tags=[],
                )[0]
            )
            self.assertFalse(
                mgr._passes_filters(
                    title="R18MMD",
                    likes=0,
                    views=0,
                    published_at="",
                    tags=[],
                )[0]
            )
        finally:
            app_config.filter_title_include = old_include
            app_config.filter_title_exclude = old_exclude

    def test_subscription_source_sort_and_filter_text_use_author_names(self):
        sources = [
            {"source_type": "author", "source_key": "zeta", "title": "Zeta"},
            {"source_type": "author", "source_key": "alice", "title": "Alice"},
            {"source_type": "playlist", "source_key": "plist01", "title": "Playlist B"},
        ]

        ordered = sorted(sources, key=_source_sort_key)

        self.assertEqual([source["source_key"] for source in ordered], ["alice", "plist01", "zeta"])
        self.assertIn("alice", _source_search_text(sources[1]))
        self.assertIn("iwara.tv/profile/alice", _source_search_text(sources[1]))

    def test_subscription_source_sort_supports_import_time_and_numeric_fields(self):
        sources = [
            {
                "source_type": "author",
                "source_key": "old",
                "title": "Same",
                "created_at": "2025-01-01 00:00:00",
                "new_count": 2,
            },
            {
                "source_type": "author",
                "source_key": "new",
                "title": "Same",
                "created_at": "2025-02-01 00:00:00",
                "new_count": 8,
            },
        ]

        by_import_time = sorted(sources, key=lambda source: _source_sort_key(source, "created_at"))
        by_new_count = sorted(sources, key=lambda source: _source_sort_key(source, "new_count"), reverse=True)

        self.assertEqual([source["source_key"] for source in by_import_time], ["old", "new"])
        self.assertEqual([source["source_key"] for source in by_new_count], ["new", "old"])


def tearDownModule():
    for path in TEMP_DIRS:
        shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
