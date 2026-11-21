"""Automatic retry of failed downloads, decided by the error message (issue #30)."""

import os
import shutil
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.config import app_config
from app.core.download.manager import DownloadManager
from app.core.download.retry_policy import RETRY, SKIP, classify_failure, should_auto_retry, split_keywords
from app.core.download.task_queue import TaskQueueStore
from app.core.history import DownloadHistory
from app.core.models import DownloadTask, TaskStatus
from app.core.subscription.store import SubscriptionStore

INCOMPLETE_READ = (
    "('Connection broken: IncompleteRead(8288950 bytes read, 83336564 more expected)', "
    "IncompleteRead(8288950 bytes read, 83336564 more expected))"
)
_KEYS = (
    "max_concurrent",
    "skip_existing_files",
    "auto_restore_stalled_cancelled",
    "auto_retry_failed_enabled",
    "auto_retry_failed_max_attempts",
    "auto_retry_failed_retry_keywords",
    "auto_retry_failed_skip_keywords",
)


class RetryPolicyTests(unittest.TestCase):
    def test_network_errors_are_retried(self):
        for reason in (
            INCOMPLETE_READ,
            "HTTPSConnectionPool(host='x'): Read timed out. (read timeout=60)",
            "HTTP 503: Service Unavailable",
            "下载不完整（缺少 12 MB），重试可继续。",
            "Incomplete download (missing 12 MB). Retry to resume.",
        ):
            with self.subTest(reason=reason):
                self.assertEqual(classify_failure(reason), RETRY)
                self.assertTrue(should_auto_retry(reason))

    def test_permission_and_missing_errors_are_not_retried(self):
        for reason in (
            "私有作品，当前登录账号没有权限下载。",
            "作品不存在或当前账号不可见，可能已删除、隐藏、私有，或需要换有权限的账号登录。",
            "该账号无法观看此视频",
            "Private video. Please login and try again.",
            "非公開動画です。現在のログインアカウントには保存権限がありません。",
            "该视频为 YouTube 嵌入，无法下载（https://youtu.be/x）",
            "HTTP 403: Forbidden",
        ):
            with self.subTest(reason=reason):
                self.assertEqual(classify_failure(reason), SKIP)
                self.assertFalse(should_auto_retry(reason))

    def test_unknown_errors_are_not_retried_by_default(self):
        self.assertEqual(classify_failure("something odd happened"), "")
        self.assertFalse(should_auto_retry("something odd happened"))
        self.assertFalse(should_auto_retry(""))

    def test_user_keywords_override_the_built_in_rules(self):
        self.assertTrue(should_auto_retry("HTTP 403: Forbidden", retry_keywords="http 403"))
        self.assertTrue(should_auto_retry("something odd happened", retry_keywords="odd"))
        self.assertFalse(should_auto_retry(INCOMPLETE_READ, skip_keywords="IncompleteRead"))
        # "never" wins over "always"
        self.assertFalse(should_auto_retry(INCOMPLETE_READ, retry_keywords="broken", skip_keywords="broken"))

    def test_split_keywords_accepts_common_separators(self):
        self.assertEqual(split_keywords(" A, b，c;d；\ne ,, "), ["a", "b", "c", "d", "e"])


class AutoRetryManagerTests(unittest.TestCase):
    def setUp(self):
        self._saved = {key: getattr(app_config, key) for key in _KEYS}
        app_config.max_concurrent = 3
        app_config.skip_existing_files = False
        app_config.auto_restore_stalled_cancelled = False
        app_config.auto_retry_failed_enabled = True
        app_config.auto_retry_failed_max_attempts = 2
        app_config.auto_retry_failed_retry_keywords = ""
        app_config.auto_retry_failed_skip_keywords = ""
        self._tmp = tempfile.mkdtemp(prefix="iwaratool-retry-")
        self.mgr = DownloadManager()
        self.mgr._watchdog_stop.set()  # drive the idle check by hand
        self.mgr.history = DownloadHistory(os.path.join(self._tmp, "history.db"))
        self.mgr.subscriptions = SubscriptionStore(os.path.join(self._tmp, "subscriptions.db"))
        self.submitted = []
        self.mgr._resolve_executor = type("Exec", (), {"submit": lambda _s, fn, *a, **k: self.submitted.append(a)})()

    def tearDown(self):
        for key, value in self._saved.items():
            setattr(app_config, key, value)
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _add(self, task_id: str, status: TaskStatus, error: str = "") -> DownloadTask:
        task = DownloadTask(task_id, "", task_id, status=status, error_msg=error)
        with self.mgr._lock:
            self.mgr._tasks[task_id] = task
            self.mgr._task_id_by_video_id[task.video_id] = task_id
            if status in (TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.COMPLETED):
                self.mgr._mark_terminal_locked(task)
            elif status == TaskStatus.RESOLVING:
                self.mgr._active_task_ids.add(task_id)
        return task

    def _fail_again(self, task: DownloadTask, error: str):
        with self.mgr._lock:
            self.mgr._active_task_ids.discard(task.task_id)
            task.status = TaskStatus.FAILED
            task.error_msg = error
            self.mgr._mark_terminal_locked(task)

    def test_only_transient_failures_are_retried_once_idle(self):
        network = self._add("network", TaskStatus.FAILED, INCOMPLETE_READ)
        denied = self._add("denied", TaskStatus.FAILED, "私有作品，当前登录账号没有权限下载。")
        busy = self._add("busy", TaskStatus.RESOLVING)
        self._add("waiting", TaskStatus.STAGED)  # the workbench does not keep the queue busy

        self.assertEqual(self.mgr._auto_retry_failed_if_idle(), 0)
        with self.mgr._lock:
            self.mgr._active_task_ids.discard(busy.task_id)
            busy.status = TaskStatus.COMPLETED
            self.mgr._mark_terminal_locked(busy)
        self.assertEqual(self.mgr._auto_retry_failed_if_idle(), 1)

        self.assertEqual(network.status, TaskStatus.RESOLVING)
        self.assertEqual(network.error_msg, "")
        self.assertEqual(network.auto_retry_count, 1)
        self.assertEqual(denied.status, TaskStatus.FAILED)
        self.assertEqual(self.submitted, [("network",)])

    def test_attempts_stop_at_the_limit_until_a_manual_retry(self):
        task = self._add("network", TaskStatus.FAILED, INCOMPLETE_READ)
        for _ in range(2):
            self.assertEqual(self.mgr._auto_retry_failed_if_idle(), 1)
            self._fail_again(task, INCOMPLETE_READ)
        self.assertEqual(self.mgr._auto_retry_failed_if_idle(), 0)
        self.assertEqual(task.auto_retry_count, 2)

        self.mgr.retry_task(task.task_id)
        self.assertEqual(task.auto_retry_count, 0)

    def test_disabled_switch_and_skip_keywords_block_retry(self):
        self._add("network", TaskStatus.FAILED, INCOMPLETE_READ)
        app_config.auto_retry_failed_enabled = False
        self.assertEqual(self.mgr._auto_retry_failed_if_idle(), 0)
        app_config.auto_retry_failed_enabled = True
        app_config.auto_retry_failed_skip_keywords = "IncompleteRead"
        self.assertEqual(self.mgr._auto_retry_failed_if_idle(), 0)

    def test_partial_file_is_kept_for_resume(self):
        temp_path = os.path.join(self._tmp, "video.mp4_temp")
        with open(temp_path, "wb") as stream:
            stream.write(b"partial")
        task = self._add("network", TaskStatus.FAILED, INCOMPLETE_READ)
        task.file_path = os.path.join(self._tmp, "video.mp4")

        self.assertEqual(self.mgr._auto_retry_failed_if_idle(), 1)
        self.assertTrue(os.path.exists(temp_path))

    def test_retry_count_survives_a_restart(self):
        task = DownloadTask("t", "", "t", status=TaskStatus.FAILED, error_msg=INCOMPLETE_READ, auto_retry_count=2)
        store = TaskQueueStore(os.path.join(self._tmp, "tasks.json"))
        store.save([task])
        (loaded,) = store.load()
        self.assertEqual(loaded.auto_retry_count, 2)

    def test_stalled_restore_ignores_tasks_waiting_on_the_workbench(self):
        app_config.auto_restore_stalled_cancelled = True
        stalled = self._add("stalled", TaskStatus.CANCELLED)
        stalled.cancel_origin = "auto_stall"
        self._add("waiting", TaskStatus.STAGED)
        self.assertEqual(self.mgr._restore_auto_stalled_cancelled_if_idle(), 1)


if __name__ == "__main__":
    unittest.main()
