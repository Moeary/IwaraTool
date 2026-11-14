import json
import os
import tempfile
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from app.config import AppConfig, app_config
from app.core.manager import DownloadManager
from app.core.models import DownloadTask, TaskStatus
from app.core.rules import (
    BUILTIN_DEFAULT_RULE_ID,
    PAGE_RULE_FOLLOW_DEFAULT,
    TEMPORARY_RULE_ID,
    RuleStore,
    default_rule_payload,
    ensure_temporary_rule,
    page_rule_id,
    set_page_rule_choice,
    temporary_rule,
)
from app.core.task_queue import TaskQueueStore


class _IsolatedRules(unittest.TestCase):
    """A private rules.json and UI-value store for each test."""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = self._temp.name
        self.store = RuleStore(os.path.join(self.root, "rules.json"))
        self.ui_values: dict[str, str] = {}
        stack = ExitStack()
        self.addCleanup(stack.close)
        for target in (
            "app.core.rules.rule_store",
            "app.core.download.existing_files.rule_store",
            "app.core.download.task_rules.rule_store",
        ):
            stack.enter_context(patch(target, self.store))
        stack.enter_context(patch.object(app_config, "get_ui_value", lambda key, default="": self.ui_values.get(key, default)))
        stack.enter_context(patch.object(app_config, "set_ui_value", lambda key, value, **_: self.ui_values.__setitem__(key, value)))


class PageRuleChoiceTests(_IsolatedRules):
    def test_temporary_rule_is_seeded_once_and_stays_deleted(self):
        self.assertTrue(ensure_temporary_rule())
        rule = self.store.find(TEMPORARY_RULE_ID)
        self.assertIsNotNone(rule)
        self.assertFalse(rule["payload"]["record_to_history"])
        self.assertTrue(rule["payload"]["filename_template"].startswith("temp_video/"))

        self.assertTrue(self.store.delete(TEMPORARY_RULE_ID))
        self.assertFalse(ensure_temporary_rule())
        self.assertIsNone(self.store.find(TEMPORARY_RULE_ID))

    def test_search_and_detail_default_to_the_temporary_rule(self):
        ensure_temporary_rule()
        self.assertEqual(page_rule_id("search"), TEMPORARY_RULE_ID)
        self.assertEqual(page_rule_id("detail"), TEMPORARY_RULE_ID)
        self.assertEqual(page_rule_id("subscription"), BUILTIN_DEFAULT_RULE_ID)

    def test_page_choice_is_independent_and_falls_back_when_rule_is_gone(self):
        mine = self.store.save("作者归档", default_rule_payload())
        set_page_rule_choice("subscription", mine["id"])
        self.assertEqual(page_rule_id("subscription"), mine["id"])
        self.assertEqual(page_rule_id("home"), BUILTIN_DEFAULT_RULE_ID)

        set_page_rule_choice("search", PAGE_RULE_FOLLOW_DEFAULT)
        self.assertEqual(page_rule_id("search"), BUILTIN_DEFAULT_RULE_ID)

        self.store.delete(mine["id"])
        self.assertEqual(page_rule_id("subscription"), BUILTIN_DEFAULT_RULE_ID)


class TemporaryDownloadTests(_IsolatedRules):
    def setUp(self):
        super().setUp()
        ensure_temporary_rule()
        self.manager = DownloadManager(task_store=TaskQueueStore(os.path.join(self.root, "tasks.json")))
        self.addCleanup(self.manager.shutdown, wait=False)
        self.manager._try_activate = lambda: None
        self.temp_payload = json.dumps(temporary_rule()["payload"])

    def _completed(self, video_id: str, payload_json: str) -> DownloadTask:
        task = DownloadTask(
            f"task-{video_id}",
            f"https://www.iwara.tv/video/{video_id}",
            video_id,
            status=TaskStatus.COMPLETED,
            rule_id=TEMPORARY_RULE_ID,
            rule_payload_json=payload_json,
            file_path=os.path.join(self.root, "temp_video", f"t_{video_id}.mp4"),
        )
        with self.manager._lock:
            self.manager._tasks[task.task_id] = task
            self.manager._task_id_by_video_id[video_id.lower()] = task.task_id
        return task

    def test_finished_temporary_copy_does_not_block_a_real_download(self):
        old = self._completed("tempVideo01", self.temp_payload)
        with patch.object(AppConfig, "skip_existing_files", False):
            queued = self.manager.enqueue_video_ids(["tempVideo01"], rule_id=BUILTIN_DEFAULT_RULE_ID)
        self.assertEqual(queued, 1)
        self.assertIsNone(self.manager.get_task(old.task_id))
        new_task = self.manager.get_task(self.manager._task_id_by_video_id["tempvideo01"])
        self.assertEqual(new_task.rule_id, BUILTIN_DEFAULT_RULE_ID)

    def test_finished_recorded_download_still_counts_as_duplicate(self):
        self._completed("keptVideo01", json.dumps(default_rule_payload()))
        with patch.object(AppConfig, "skip_existing_files", False):
            queued = self.manager.enqueue_video_ids(["keptVideo01"], rule_id=BUILTIN_DEFAULT_RULE_ID)
        self.assertEqual(queued, 0)

    def test_temporary_copy_is_not_indexed_as_already_downloaded(self):
        download_root = os.path.join(self.root, "下载 根目录")
        for folder, name in (("temp_video", "title tempVideo02.mp4"), ("author", "title keptVideo02.mp4")):
            os.makedirs(os.path.join(download_root, folder), exist_ok=True)
            with open(os.path.join(download_root, folder, name), "wb") as stream:
                stream.write(b"x")
        with patch.object(AppConfig, "download_dir", download_root):
            index = self.manager._get_existing_file_index()
        self.assertIn("keptvideo02", index)
        self.assertNotIn("tempvideo02", index)

    def test_completing_a_temporary_copy_skips_history_and_index(self):
        task = self._completed("tempVideo03", self.temp_payload)
        task.status = TaskStatus.DOWNLOADING
        with patch.object(self.manager.history, "upsert_downloaded") as upsert:
            self.manager._complete_task(task.task_id)
        upsert.assert_not_called()
        self.assertNotIn("tempvideo03", self.manager._existing_file_index)
        self.assertTrue(self.manager.is_temporary_copy(task))


if __name__ == "__main__":
    unittest.main()
