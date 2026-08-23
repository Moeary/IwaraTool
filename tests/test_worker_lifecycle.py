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
