"""Automatic retry of failed downloads, decided by the error message (issue #30)."""

import os
import shutil
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.config import app_config
from app.core.download.integrity import (
    read_resume_meta,
    resume_meta_path,
    resume_source_mismatch,
    resume_total_mismatch,
    write_resume_meta,
)
from app.core.download.manager import DownloadManager
from app.core.download.manager_state import STALL_AUTO_RESTORE_LIMIT
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
            "HTTP 520: ",
            "503 Server Error: Service Unavailable for url: https://example",
            "HTTP 503: Permission service temporarily unavailable",
            "Local partial file does not match the server file; it was removed. Retry to download again.",
            "断点续传失败（server resumed at byte 0, expected 5），已清除缓存；请重试以重新下载。",
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
            "HTTP 401: 网络登录已失效",
            "HTTP 401: Unauthorized",
        ):
            with self.subTest(reason=reason):
                self.assertEqual(classify_failure(reason), SKIP)
                self.assertFalse(should_auto_retry(reason))

    def test_unknown_errors_are_not_retried_by_default(self):
        self.assertEqual(classify_failure("something odd happened"), "")
        self.assertEqual(classify_failure("received 500 of 1000 bytes"), "")
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
        self.mgr._started = True
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
        self.mgr._release_task_worker(task.task_id)  # the fake executor never runs the job
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

    def test_nothing_is_retried_before_start(self):
        self._add("network", TaskStatus.FAILED, INCOMPLETE_READ)
        self.mgr._started = False
        self.assertEqual(self.mgr._auto_retry_failed_if_idle(), 0)
        self.mgr._started = True
        self.mgr._shutting_down = True
        self.assertEqual(self.mgr._auto_retry_failed_if_idle(), 0)

    def test_task_with_a_live_job_is_not_requeued(self):
        app_config.auto_restore_stalled_cancelled = True
        stalled = self._add("stalled", TaskStatus.CANCELLED)
        stalled.cancel_origin = "auto_stall"
        self._add("network", TaskStatus.FAILED, INCOMPLETE_READ)
        with self.mgr._lock:
            self.mgr._claim_task_worker_locked("stalled")  # its blocked worker has not returned
            self.mgr._claim_task_worker_locked("network")
        self.assertEqual(self.mgr._auto_requeue_if_idle(), (0, 0))

        self.mgr._release_task_worker("stalled")
        self.mgr._release_task_worker("network")
        self.assertEqual(self.mgr._auto_requeue_if_idle(), (1, 1))

    def test_manual_retry_claim_blocks_automatic_retry(self):
        task = self._add("network", TaskStatus.FAILED, INCOMPLETE_READ)
        with self.mgr._lock:
            self.mgr._manual_retry_claims.add(task.task_id)
        self.assertEqual(self.mgr._auto_retry_failed_if_idle(), 0)

        seen_during_cleanup = []

        def cleanup(title, file_path):
            # the watchdog runs while the manual retry is removing temp files
            seen_during_cleanup.append(self.mgr._auto_retry_failed_if_idle())
            return 0, 0

        with self.mgr._lock:
            self.mgr._manual_retry_claims.clear()
        self.mgr._log_retry_temp_cleanup = cleanup
        self.mgr.retry_task(task.task_id)
        self.assertEqual(seen_during_cleanup, [0])
        self.assertEqual(task.auto_retry_count, 0)
        self.assertEqual(self.mgr._manual_retry_claims, set())
        self.assertEqual(self.submitted, [("network",)])

    def test_stalled_and_failed_tasks_are_requeued_in_the_same_pass(self):
        app_config.auto_restore_stalled_cancelled = True
        stalled = self._add("stalled", TaskStatus.CANCELLED)
        stalled.cancel_origin = "auto_stall"
        network = self._add("network", TaskStatus.FAILED, INCOMPLETE_READ)
        self.assertEqual(self.mgr._auto_requeue_if_idle(), (1, 1))
        self.assertEqual(stalled.auto_restore_count, 1)
        self.assertEqual(network.auto_retry_count, 1)

    def test_stall_restores_stop_at_the_limit(self):
        app_config.auto_restore_stalled_cancelled = True
        stalled = self._add("stalled", TaskStatus.CANCELLED)
        stalled.cancel_origin = "auto_stall"
        stalled.auto_restore_count = STALL_AUTO_RESTORE_LIMIT
        self.assertEqual(self.mgr._restore_auto_stalled_cancelled_if_idle(), 0)
        self.assertTrue(self.mgr.restore_cancelled_task("stalled"))
        self.assertEqual(stalled.auto_restore_count, 0)


class _Resp:
    def __init__(self, status_code, headers=None):
        self.status_code = status_code
        self.headers = headers or {}
        self.text = ""


class ResumeIdentityTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="iwaratool-resume-")
        self.mgr = DownloadManager()
        self.mgr._watchdog_stop.set()
        self.final = os.path.join(self._tmp, "video.mp4")
        self.temp = f"{self.final}_temp"
        with open(self.temp, "wb") as stream:
            stream.write(b"x" * 1000)
        self.task = DownloadTask("t", "", "t", status=TaskStatus.DOWNLOADING, file_id="file-a", quality="Source")
        self.task.file_path = self.final
        self.task.download_url = "https://example.invalid/file"
        self.mgr._tasks["t"] = self.task
        self.failures = []
        self.mgr._fail_task = lambda task_id, reason: self.failures.append(reason)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_meta_helpers(self):
        write_resume_meta(self.temp, "file-a:Source", 2000)
        self.assertEqual(read_resume_meta(self.temp), {"source": "file-a:Source", "total": 2000})
        self.assertTrue(resume_source_mismatch({"source": "file-a:Source"}, "file-b:Source"))
        self.assertFalse(resume_source_mismatch({}, "file-b:Source"))  # older partial files
        self.assertEqual(resume_total_mismatch({"Content-Range": "bytes 1000-1999/2000"}, 1000, 2000), "")
        self.assertIn("3000", resume_total_mismatch({"Content-Range": "bytes 1000-2999/3000"}, 1000, 2000))
        self.assertIn("2500", resume_total_mismatch({"Content-Length": "1500"}, 1000, 2000))
        self.assertEqual(resume_total_mismatch({"Content-Range": "bytes 1000-2999/3000"}, 1000, 0), "")

    def test_partial_file_of_another_source_is_discarded_before_resuming(self):
        write_resume_meta(self.temp, "file-a:540", 2000)
        sent = {}

        def get(url, headers=None, **_kwargs):
            sent.update(headers or {})
            return _Resp(200)

        self.mgr.api.scraper.get = get
        _resp, existing = self.mgr._open_download_response(self.task, "t", self.temp)
        self.assertEqual(existing, 0)
        self.assertNotIn("Range", sent)
        self.assertFalse(os.path.exists(self.temp))
        self.assertFalse(os.path.exists(resume_meta_path(self.temp)))

    def test_same_source_resumes(self):
        write_resume_meta(self.temp, "file-a:Source", 2000)
        sent = {}
        self.mgr.api.scraper.get = lambda url, headers=None, **_k: sent.update(headers or {}) or _Resp(206)
        _resp, existing = self.mgr._open_download_response(self.task, "t", self.temp)
        self.assertEqual(existing, 1000)
        self.assertEqual(sent.get("Range"), "bytes=1000-")

    def test_206_for_a_different_total_restarts(self):
        write_resume_meta(self.temp, "file-a:Source", 2000)
        resp = _Resp(206, {"Content-Range": "bytes 1000-2999/3000", "Content-Length": "2000"})
        self.assertFalse(self.mgr._accept_download_response("t", resp, 1000, self.temp, self.final))
        self.assertEqual(len(self.failures), 1)
        self.assertFalse(os.path.exists(self.temp))

    def test_416_without_total_is_not_trusted_unless_recorded(self):
        write_resume_meta(self.temp, "file-a:Source", 2000)
        self.assertFalse(self.mgr._accept_download_response("t", _Resp(416), 1000, self.temp, self.final))
        self.assertEqual(len(self.failures), 1)
        self.assertFalse(os.path.exists(self.temp))
        self.assertFalse(os.path.exists(self.final))


if __name__ == "__main__":
    unittest.main()
