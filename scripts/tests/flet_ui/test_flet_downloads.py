"""The Downloads page's store and service against a fake manager (no window)."""

from __future__ import annotations

import asyncio
import importlib.util
import unittest
from types import SimpleNamespace

from app.core.models import DownloadTask, TaskStatus
from app.presentation.downloads.staging import batch_counts_text, confirmed_text
from app.presentation.downloads.task_text import task_cover_url, task_meta_text

HAS_FLET = importlib.util.find_spec("flet") is not None


def task(task_id: str, status: TaskStatus, **fields) -> DownloadTask:
    return DownloadTask(task_id=task_id, url=f"https://www.iwara.tv/video/{task_id}", video_id=task_id, status=status, **fields)


class FakeManager:
    def __init__(self, tasks):
        self.tasks = tasks
        self.calls = []

    def get_tasks(self):
        return list(self.tasks)

    def cancel_task(self, task_id):
        self.calls.append(("cancel_task", task_id))
        return True

    def confirm_staged(self, *, batch_id=""):
        self.calls.append(("confirm_staged", batch_id))
        return {"queued": 2, "marked": 0, "dropped": 1}


class PresentationTests(unittest.TestCase):
    def test_cover_url_uses_the_small_rendition_on_the_image_host(self):
        t = task("a", TaskStatus.DOWNLOADING, file_id="f1", file_url="https://files.iwara.tv/file/f1?x", thumbnail_index=3)
        self.assertEqual(task_cover_url(t), "https://i.iwara.tv/image/thumbnail/f1/thumbnail-03.jpg")
        t.thumbnail_url = "https://i.iwara.tv/image/original/f1/thumbnail-03.jpg"
        self.assertEqual(task_cover_url(t), "https://i.iwara.tv/image/thumbnail/f1/thumbnail-03.jpg")
        self.assertEqual(task_cover_url(task("b", TaskStatus.QUEUED_META)), "")

    def test_meta_line_shows_speed_only_while_downloading_and_errors_on_failure(self):
        down = task("a", TaskStatus.DOWNLOADING, author="ann", speed_str="1 MB/s")
        self.assertIn("1 MB/s", task_meta_text(down))
        failed = task("b", TaskStatus.FAILED, author="ann", speed_str="1 MB/s", error_msg="boom")
        self.assertNotIn("1 MB/s", task_meta_text(failed))
        self.assertIn("boom", task_meta_text(failed))

    def test_staging_texts(self):
        title, content = confirmed_text({"queued": 2, "marked": 1, "dropped": 3})
        self.assertTrue(title)
        self.assertIn("2", content)
        self.assertIn("3", batch_counts_text({"ready": 3, "skip": 1}))
        self.assertNotEqual(batch_counts_text({"ready": 3, "skip": 1}), batch_counts_text({"ready": 3, "skip": 1, "unavailable": 2}))


@unittest.skipUnless(HAS_FLET, "Flet is not installed in this environment")
class TaskStoreTests(unittest.TestCase):
    def make(self, tasks):
        from app.ui_flet.stores.tasks import TaskStore

        store = TaskStore(FakeManager(tasks))
        asyncio.run(store.reload())
        return store

    def test_staged_tasks_stay_out_and_the_order_follows_the_status_priority(self):
        from app.ui_flet.stores.tasks import TAB_ACTIVE, TAB_ALL, TAB_FAILED, TAB_QUEUED

        store = self.make([
            task("done", TaskStatus.COMPLETED),
            task("staged", TaskStatus.STAGED),
            task("q", TaskStatus.QUEUED_META),
            task("dl", TaskStatus.DOWNLOADING),
            task("bad", TaskStatus.FAILED),
        ])
        self.assertNotIn("staged", store.index.order)
        self.assertEqual(store.index.order.index("dl") < store.index.order.index("done"), True)
        self.assertEqual(store.index.counts[TAB_ALL], 4)
        self.assertEqual((store.index.counts[TAB_ACTIVE], store.index.counts[TAB_QUEUED], store.index.counts[TAB_FAILED]), (1, 1, 1))
        store.set_tab(TAB_FAILED)
        self.assertEqual(store.visible_ids(), ["bad"])
        store.set_tab("nonsense")
        self.assertEqual(store.index.tab, TAB_ALL)

    def test_query_filters_on_title_and_author(self):
        store = self.make([task("a", TaskStatus.COMPLETED, title="Ocean Wave", author="ann"),
                           task("b", TaskStatus.COMPLETED, title="Night", author="bob")])
        store.set_query("ocean")
        self.assertEqual(store.visible_ids(), ["a"])
        store.set_query("BOB")
        self.assertEqual(store.visible_ids(), ["b"])

    def test_progress_updates_one_item_and_a_reload_drops_removed_tasks(self):
        manager_tasks = [task("a", TaskStatus.DOWNLOADING, total_bytes=100), task("b", TaskStatus.QUEUED_META)]
        store = self.make(manager_tasks)
        before = store.items["b"].task
        store.on_progress("a", 40, 100, "2 MB/s")
        self.assertEqual(store.items["a"].task.downloaded_bytes, 40)
        self.assertEqual(manager_tasks[0].downloaded_bytes, 0)  # the manager's copy is never touched
        self.assertIs(store.items["b"].task, before)
        store.on_progress("missing", 1, 2, "")
        store.manager.tasks = [manager_tasks[0]]
        asyncio.run(store.reload())
        self.assertNotIn("b", store.items)

    def test_log_keeps_a_bounded_tail_and_counts_errors(self):
        from app.ui_flet.stores.tasks import LOG_LIMIT

        store = self.make([])
        for i in range(LOG_LIMIT + 5):
            store.on_log(f"line {i}")
        store.on_log("Download error: x")
        self.assertEqual(len(store.log.lines), LOG_LIMIT)
        self.assertEqual(store.log.lines[-1], "Download error: x")
        self.assertEqual(store.log.unseen_errors, 1)


@unittest.skipUnless(HAS_FLET, "Flet is not installed in this environment")
class DownloadServiceTests(unittest.TestCase):
    def setUp(self):
        from app.ui_flet.services.downloads import DownloadService

        self.notices = []
        self.manager = FakeManager([])
        shell = SimpleNamespace(page=None, state=SimpleNamespace(destination="downloads"))
        self.service = DownloadService(shell, manager=self.manager)
        self.service._notice = lambda kind, title, content="", **_k: self.notices.append((kind, title))

    def test_task_controls_run_off_the_loop(self):
        import threading

        seen = []

        def cancel(task_id):
            seen.append(threading.current_thread() is threading.main_thread())
            return True

        self.manager.cancel_task = cancel
        self.assertTrue(asyncio.run(self.service.run("cancel_task", "a")))
        self.assertEqual(seen, [False])

    def test_confirm_passes_the_batch(self):
        result = asyncio.run(self.service.confirm_staged("b1"))
        self.assertEqual(result["queued"], 2)
        self.assertEqual(self.manager.calls, [("confirm_staged", "b1")])

    def test_blank_text_only_warns(self):
        self.assertFalse(asyncio.run(self.service.submit_links("   ", rule_id="")))
        self.assertEqual(self.notices[0][0], "warning")


if __name__ == "__main__":
    unittest.main()
