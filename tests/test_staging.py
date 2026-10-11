"""Videos waiting on the download workbench for confirmation (TaskStatus.STAGED)."""

import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.config import app_config
from app.core.download.staging import STAGE_READY, STAGE_SKIP, STAGE_UNAVAILABLE
from app.core.history import DownloadHistory
from app.core.manager import DownloadManager
from app.core.models import TaskStatus
from app.core.subscription.store import SubscriptionStore
from app.core.task_queue import TaskQueueStore


class _NoExecutor:
    def submit(self, *_args, **_kwargs):
        return None


VIDEOS = [
    {"id": "have0000001", "title": "Here already"},
    {"id": "priv0000002", "title": "Secret", "private": True},
    {"id": "new00000003", "title": "Fresh", "numLikes": 3},
]


class _StagingCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="iwaratool-staging-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        saved = (app_config.download_dir, app_config.skip_existing_files)
        self.addCleanup(lambda: (setattr(app_config, "download_dir", saved[0]), setattr(app_config, "skip_existing_files", saved[1])))
        app_config.download_dir = os.path.join(self.tmp, "downloads")
        os.makedirs(app_config.download_dir)
        app_config.skip_existing_files = True
        self.mgr = self._manager()
        path = os.path.join(app_config.download_dir, "have0000001.mp4")
        with open(path, "wb") as fh:
            fh.write(b"x")
        self.mgr.history.upsert_downloaded({"video_id": "have0000001", "file_path": path})

    def _manager(self) -> DownloadManager:
        mgr = DownloadManager(task_store=TaskQueueStore(os.path.join(self.tmp, "tasks.json")))
        mgr.history = DownloadHistory(os.path.join(self.tmp, "history.db"))
        mgr.subscriptions = SubscriptionStore(os.path.join(self.tmp, "subscriptions.db"))
        mgr._resolve_executor = _NoExecutor()
        mgr._existing_file_index_built_at = 0
        return mgr

    def _stage(self, videos=VIDEOS, **kwargs):
        return self.mgr.stage_preview(self.mgr.preview_videos([dict(v) for v in videos], label="bob"), **kwargs)

    def _verdicts(self):
        return {t.video_id: t.stage_verdict for t in self.mgr.get_tasks() if t.status == TaskStatus.STAGED}


class StagingTests(_StagingCase):
    def test_staged_videos_wait_with_their_verdicts(self):
        result = self._stage()
        self.assertEqual((result["ready"], result["skip"], result["unavailable"]), (1, 1, 1))
        self.assertEqual(self._verdicts(), {
            "have0000001": STAGE_SKIP, "priv0000002": STAGE_UNAVAILABLE, "new00000003": STAGE_READY,
        })
        self.assertEqual(self.mgr.pending_task_count(), 0)  # nothing to stop on exit
        self.assertEqual(list(self.mgr._queued_meta_ids), [])  # the scheduler never sees them
        self.assertTrue(self.mgr.staged_example()["relative_path"].endswith("new00000003.mp4"))

    def test_confirm_queues_the_ready_ones_and_clears_the_batch(self):
        batch = self._stage()["batch_id"]
        result = self.mgr.confirm_staged(batch_id=batch)
        self.assertEqual((result["queued"], result["dropped"]), (1, 2))
        statuses = {t.video_id: t.status for t in self.mgr.get_tasks()}
        self.assertEqual(statuses, {"new00000003": TaskStatus.RESOLVING})

    def test_adding_the_same_video_again_moves_it_instead_of_duplicating(self):
        self._stage()
        again = self._stage(VIDEOS[2:])
        self.assertEqual((again["staged"], len(self.mgr.get_tasks())), (1, 3))
        self.assertEqual(len(self.mgr.staged_batches()), 2)

    def test_a_direct_enqueue_promotes_the_waiting_row(self):
        self._stage()
        self.assertEqual(self.mgr.enqueue_video_ids(["new00000003"]), 1)
        task = next(t for t in self.mgr.get_tasks() if t.video_id == "new00000003")
        self.assertEqual(task.status, TaskStatus.RESOLVING)
        self.assertEqual(len(self.mgr.get_tasks()), 3)

    def test_changing_the_rule_rechecks_offline(self):
        self._stage()
        strict = {"filter_enabled": True, "filter_min_likes_enabled": True, "filter_min_likes": 10}
        self.mgr._api_call = lambda *a, **k: self.fail("no network")
        with patch.object(DownloadManager, "_rule_payload_for_id", return_value=strict):
            self.assertEqual(self.mgr.restage("strict"), 3)
        self.assertEqual(self._verdicts()["new00000003"], STAGE_SKIP)
        forced = [t.task_id for t in self.mgr.get_tasks() if t.video_id == "new00000003"]
        self.assertEqual(self.mgr.force_staged_ready(forced), 1)
        self.assertEqual(self._verdicts()["new00000003"], STAGE_READY)

    def test_a_single_ready_video_can_skip_the_wait(self):
        result = self._stage(VIDEOS[2:], direct_single=True)
        self.assertTrue(result["direct"])
        self.assertEqual(self._verdicts(), {})

    def test_staged_videos_survive_a_restart(self):
        self._stage()
        self.mgr._task_persistence.flush()
        restored = self._manager()
        restored._load_persisted_tasks()
        staged = {t.video_id: (t.status, t.stage_verdict, t.batch_label) for t in restored.get_tasks()}
        self.assertEqual(staged["new00000003"], (TaskStatus.STAGED, STAGE_READY, "bob"))
        self.assertEqual(list(restored._queued_meta_ids), [])

    def test_discard_and_remove(self):
        self._stage()
        task_id = next(t.task_id for t in self.mgr.get_tasks() if t.video_id == "priv0000002")
        self.mgr.remove_task(task_id)  # removing a waiting row just drops it
        self.assertNotIn("priv0000002", self._verdicts())
        self.assertEqual(self.mgr.discard_staged(), 2)
        self.assertEqual(self.mgr.get_tasks(), [])

    def test_old_pending_batches_are_migrated_once(self):
        import json

        data_dir = os.path.join(self.tmp, "data")
        os.makedirs(data_dir)
        with open(os.path.join(data_dir, "pending_downloads.json"), "w", encoding="utf-8") as fh:
            json.dump([{"id": "s1", "label": "old", "rule_id": "", "items": [
                {"video_id": "new00000003", "raw": {"id": "new00000003", "title": "Fresh"}, "checked": True},
                {"video_id": "priv0000002", "raw": {"id": "priv0000002"}, "checked": False},
            ]}], fh)
        with patch.object(app_config, "_data_dir", data_dir):
            self.assertEqual(self.mgr.migrate_pending_stacks(), 1)
            self.assertEqual(self.mgr.migrate_pending_stacks(), 0)  # the file is moved aside
        self.assertEqual([b["label"] for b in self.mgr.staged_batches()], ["old"])


class StagingPanelTests(_StagingCase):
    def test_destroyed_panel_cancels_its_pending_show_callback(self):
        import sys
        import shiboken6
        from PySide6.QtWidgets import QApplication
        from app.ui.downloads import task_center
        from app.ui.downloads.staging_panel import StagingPanel

        app = QApplication.instance() or QApplication([])
        self._stage()
        with patch.object(task_center, "download_manager", self.mgr), \
                patch.object(sys, "excepthook") as errors:
            panel = StagingPanel()
            shown = []
            panel.show_requested.connect(lambda *_: shown.append(True))
            shiboken6.delete(panel)
            app.processEvents()
        self.assertEqual(shown, [])
        errors.assert_not_called()

    def test_the_panel_counts_filters_and_confirms(self):
        from PySide6.QtWidgets import QApplication

        from app.ui.downloads import task_center
        from app.ui.downloads.page import DownloadInterface

        app = QApplication.instance() or QApplication([])
        manager = patch.object(task_center, "download_manager", self.mgr)
        manager.start()
        self.addCleanup(manager.stop)
        page = DownloadInterface()
        self.addCleanup(page.deleteLater)
        panel, center = page._staging_panel, page._task_center
        self.assertTrue(panel.isHidden())
        self._stage()
        center._refresh_tasks()
        panel.refresh()
        app.processEvents()
        self.assertFalse(panel.isHidden())
        self.assertTrue(all(">1</b>" in panel._chips[k].text() for k in ("ready", "skip", "unavailable")))
        center.show_staged("unavailable")
        self.assertEqual([center._tasks_by_id[i].video_id for i in center._visible_task_ids], ["priv0000002"])
        panel._confirm()
        panel.refresh()
        self.assertTrue(panel.isHidden())
        self.assertEqual([t.status for t in self.mgr.get_tasks()], [TaskStatus.RESOLVING])


class StagingFlowTests(_StagingCase):
    def _run(self, videos, *, direct_setting: bool):
        import time

        from PySide6.QtWidgets import QApplication, QWidget

        from app.ui.downloads import staging_flow, task_center

        app = QApplication.instance() or QApplication([])
        manager = patch.object(task_center, "download_manager", self.mgr)
        manager.start()
        self.addCleanup(manager.stop)
        saved = app_config.direct_download_single
        self.addCleanup(setattr, app_config, "direct_download_single", saved)
        app_config.direct_download_single = direct_setting
        host = QWidget()
        self.addCleanup(host.deleteLater)
        flow = staging_flow.StagingFlow(host)
        results = []
        flow.staged.connect(results.append)
        payloads = [dict(v) for v in videos]
        self.assertTrue(flow.start(lambda: self.mgr.preview_videos(payloads, label="page")))
        deadline = time.time() + 5
        while flow.busy() and time.time() < deadline:
            app.processEvents()
        app.processEvents()
        return results

    def test_a_batch_waits_on_the_workbench(self):
        results = self._run(VIDEOS, direct_setting=True)
        self.assertEqual(results[0]["staged"], 3)
        self.assertEqual(self.mgr.staged_counts()["ready"], 1)

    def test_a_single_video_downloads_at_once_when_allowed(self):
        self.assertEqual(self._run(VIDEOS[2:], direct_setting=True), [])  # no "staged" result: it went straight in
        self.assertEqual([t.status for t in self.mgr.get_tasks()], [TaskStatus.RESOLVING])

    def test_a_single_video_waits_when_direct_download_is_off(self):
        self._run(VIDEOS[2:], direct_setting=False)
        self.assertEqual([t.status for t in self.mgr.get_tasks()], [TaskStatus.STAGED])


if __name__ == "__main__":
    unittest.main()
