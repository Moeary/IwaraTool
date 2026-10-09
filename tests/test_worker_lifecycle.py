import unittest

from app.ui.worker_lifecycle import stop_qthreads


class FakeThread:
    def __init__(self, *, running: bool = True, wait_result: bool = True):
        self.running = running
        self.wait_result = wait_result
        self.interruptions = 0
        self.wait_calls: list[int] = []

    def isRunning(self):
        return self.running

    def requestInterruption(self):
        self.interruptions += 1

    def wait(self, timeout_ms: int):
        self.wait_calls.append(timeout_ms)
        if self.wait_result:
            self.running = False
        return self.wait_result


class WorkerLifecycleTests(unittest.TestCase):
    def test_stop_qthreads_interrupts_waits_and_deduplicates(self):
        worker = FakeThread()

        stopped = stop_qthreads([worker, worker, None], timeout_ms=100)

        self.assertTrue(stopped)
        self.assertEqual(worker.interruptions, 1)
        self.assertEqual(len(worker.wait_calls), 1)

    def test_stop_qthreads_reports_worker_that_misses_deadline(self):
        worker = FakeThread(wait_result=False)

        stopped = stop_qthreads([worker], timeout_ms=10)

        self.assertFalse(stopped)
        self.assertEqual(worker.interruptions, 1)

    def test_stop_qthreads_ignores_idle_worker(self):
        worker = FakeThread(running=False)

        self.assertTrue(stop_qthreads([worker], timeout_ms=10))
        self.assertEqual(worker.interruptions, 0)
        self.assertEqual(worker.wait_calls, [])


if __name__ == "__main__":
    unittest.main()


class SubscriptionWorkerSignalTests(unittest.TestCase):
    """Results travel on ``result_ready``; ``finished`` stays QThread's own."""

    @classmethod
    def setUpClass(cls):
        import os
        import sys

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication(sys.argv)

    def test_workers_do_not_shadow_the_native_finished_signal(self):
        from PySide6.QtCore import QThread

        from app.ui import subscription_components as sc

        for cls in (
            sc.SubscriptionRefreshWorker,
            sc.SubscriptionImportAuthorsWorker,
            sc.SubscriptionEnqueueWorker,
            sc.SubscriptionAvatarWorker,
        ):
            self.assertIs(cls.finished, QThread.finished, cls.__name__)

    def test_refresh_page_releases_the_worker_only_after_the_thread_ends(self):
        from unittest import mock

        from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QTimer

        from app.ui import subscription_actions, subscription_components as sc

        manager = mock.Mock()
        manager.refresh_all_subscriptions.return_value = {"errors": []}

        class Host:
            _release_worker = subscription_actions.SubscriptionActionsMixin._release_worker
            _worker = None

        host = Host()
        seen = {}
        loop = QEventLoop()
        with mock.patch.object(sc, "download_manager", manager):
            worker = sc.SubscriptionRefreshWorker()
            host._worker = worker
            worker.result_ready.connect(lambda summary: seen.setdefault("held", host._worker is worker))
            worker.finished.connect(lambda: host._release_worker("_worker", worker))
            worker.finished.connect(loop.quit)
            QTimer.singleShot(5000, loop.quit)
            worker.start()
            loop.exec()
        self.assertTrue(seen["held"])  # still owned while run() was returning
        self.assertIsNone(host._worker)  # released (and deleteLater'd) on the native signal
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


class AuthSnapshotTests(unittest.TestCase):
    def test_reading_the_token_does_not_wait_for_a_request_in_flight(self):
        import threading
        import time
        from unittest import mock

        from app.core.manager import DownloadManager

        manager = object.__new__(DownloadManager)
        manager._api_lock = threading.RLock()
        manager._auth_lock = threading.Lock()
        manager.api = mock.Mock(token="tok")
        manager.api.scraper.proxies = {"https": "http://proxy"}
        entered, release = threading.Event(), threading.Event()

        def slow_request():
            with manager._api_lock:
                entered.set()
                release.wait(5)

        thread = threading.Thread(target=slow_request)
        thread.start()
        try:
            entered.wait(5)
            started = time.monotonic()
            self.assertEqual(manager._current_token(), "tok")
            self.assertTrue(manager.is_logged_in())
            client = manager.create_worker_api_client()
            with mock.patch("app.core.manager.app_config"):
                manager.set_login(False)
            self.assertLess(time.monotonic() - started, 0.2)
            self.assertEqual(client.token, "tok")
            self.assertEqual(client.scraper.proxies, {"https": "http://proxy"})
            manager.close_worker_api_client(client)
        finally:
            release.set()
            thread.join(5)


class ManagedThreadTests(unittest.TestCase):
    """A running worker survives losing its owner; it is let go only once finished."""

    @classmethod
    def setUpClass(cls):
        import os
        import sys

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication(sys.argv)

    def _spin(self, condition, timeout=5.0):
        import time

        from PySide6.QtWidgets import QApplication

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not condition():
            QApplication.processEvents()
            time.sleep(0.01)
        return condition()

    def test_destroying_the_parent_or_dropping_every_reference_does_not_kill_it(self):
        import gc
        import weakref

        from PySide6.QtCore import QCoreApplication, QEvent
        from PySide6.QtWidgets import QWidget

        from app.ui.worker_lifecycle import ManagedThread

        class Slow(ManagedThread):
            def run(self):
                self.msleep(300)

        parent = QWidget()
        worker = Slow(parent)
        worker.start()
        self.assertIsNone(worker.parent())  # detached so the parent cannot take it down
        parent.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        alive = weakref.ref(worker)
        del worker
        gc.collect()  # the process would abort here if the thread were destroyed
        self.assertIsNotNone(alive())
        self.assertTrue(alive().isRunning())
        from app.ui import worker_lifecycle

        self.assertIn(alive(), worker_lifecycle._RUNNING)
        self.assertTrue(self._spin(lambda: alive() not in worker_lifecycle._RUNNING))  # let go once finished
        self.assertTrue(alive().isFinished())
        alive().deleteLater()


class ShutdownPollerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import os
        import sys

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication(sys.argv)

    def test_every_step_is_asked_at_once_and_nothing_blocks(self):
        from app.ui.worker_lifecycle import ShutdownPoller

        calls = []
        poller = ShutdownPoller([
            lambda timeout_ms: calls.append(("a", timeout_ms)) or True,
            lambda timeout_ms: calls.append(("b", timeout_ms)) or True,
        ])
        self.assertTrue(poller.start())
        self.assertEqual(calls, [("a", 0), ("b", 0)])

    def test_waits_on_the_event_loop_and_reports_the_deadline(self):
        import time

        from PySide6.QtWidgets import QApplication

        from app.ui.worker_lifecycle import ShutdownPoller

        state = {"stopped": False}
        results = []
        poller = ShutdownPoller([lambda timeout_ms: state["stopped"]], timeout_ms=5000, interval_ms=5)
        poller.done.connect(results.append)
        self.assertFalse(poller.start())
        state["stopped"] = True
        deadline = time.monotonic() + 2
        while not results and time.monotonic() < deadline:
            QApplication.processEvents()
            time.sleep(0.005)
        self.assertEqual(results, [True])

        late = []
        stuck = ShutdownPoller([lambda timeout_ms: False], timeout_ms=30, interval_ms=5)
        stuck.done.connect(late.append)
        stuck.start()
        deadline = time.monotonic() + 2
        while not late and time.monotonic() < deadline:
            QApplication.processEvents()
            time.sleep(0.005)
        self.assertEqual(late, [False])
