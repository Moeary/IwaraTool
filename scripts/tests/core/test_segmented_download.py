"""The built-in downloader over several connections, with resumable segments."""

import os
import random
import shutil
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.download import segmented, transfer
from app.core.download.integrity import BACKEND_NATIVE, BACKEND_SEGMENTED, read_resume_meta, write_resume_meta
from app.core.download.manager import DownloadManager
from app.core.download.segmented import Segment, plan_segments, segments_from_meta, use_segments
from app.core.models import DownloadTask, TaskStatus

SIZE = 9 * 1024 * 1024 + 123  # above MULTI_MIN_BYTES, not a multiple of anything


class _Resp:
    def __init__(self, status, headers, body=b"", fail_after=None):
        self.status_code = status
        self.headers = headers
        self.text = ""
        self._body = body
        self._fail_after = fail_after

    def iter_content(self, chunk_size=65536):
        sent = 0
        for start in range(0, len(self._body), chunk_size):
            if self._fail_after is not None and sent >= self._fail_after:
                raise ConnectionError("connection reset")
            chunk = self._body[start:start + chunk_size]
            sent += len(chunk)
            yield chunk

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Server:
    """Serves ``data`` with Range support; ``broken_start`` makes the range starting there drop."""

    def __init__(self, data, ranges=True, broken_start=None, fail_after=0):
        self.data, self.ranges, self.broken_start, self.fail_after = data, ranges, broken_start, fail_after
        self.requests = []
        self._lock = threading.Lock()

    def get(self, url, headers=None, **_kwargs):
        headers = headers or {}
        with self._lock:
            self.requests.append(headers.get("Range", ""))
        common = {"Accept-Ranges": "bytes"} if self.ranges else {}
        spec = headers.get("Range")
        if not spec or not self.ranges:
            return _Resp(200, {**common, "Content-Length": str(len(self.data))}, self.data)
        start, _, end = spec.removeprefix("bytes=").partition("-")
        start, end = int(start), int(end) if end else len(self.data) - 1
        body = self.data[start:end + 1]
        fail = self.fail_after if self.broken_start is not None and start == self.broken_start else None
        return _Resp(206, {**common, "Content-Length": str(len(body)),
                           "Content-Range": f"bytes {start}-{end}/{len(self.data)}"}, body, fail)


class PlanTests(unittest.TestCase):
    def test_plan_covers_the_file_once(self):
        for total, connections in ((SIZE, 4), (SIZE, 16), (3 * 1024 * 1024, 8), (100, 4)):
            segments = plan_segments(total, connections)
            self.assertEqual(segments[0].start, 0)
            self.assertEqual(segments[-1].end, total - 1)
            for left, right in zip(segments, segments[1:]):
                self.assertEqual(left.end + 1, right.start)
        self.assertEqual(len(plan_segments(100, 4)), 1)  # too small to split
        self.assertEqual(len(plan_segments(3 * 1024 * 1024, 8)), 1)

    def test_saved_map_must_cover_the_file(self):
        meta = {"total": 10, "segments": [[0, 4, 5], [5, 9, 2]]}
        self.assertEqual(segments_from_meta(meta, 10), [Segment(0, 4, 5), Segment(5, 9, 2)])
        self.assertIsNone(segments_from_meta(meta, 11))
        self.assertIsNone(segments_from_meta({"total": 10, "segments": [[0, 4, 5], [6, 9, 0]]}, 10))  # gap
        self.assertIsNone(segments_from_meta({"total": 10, "segments": [[0, 9, 11]]}, 10))  # past the end
        self.assertIsNone(segments_from_meta({"total": 10, "segments": [["x", 9, 0]]}, 10))

    def test_only_large_files_from_range_servers_are_split(self):
        self.assertTrue(use_segments(SIZE, {"Accept-Ranges": "bytes"}, 4))
        self.assertFalse(use_segments(SIZE, {"Accept-Ranges": "bytes"}, 1))
        self.assertFalse(use_segments(SIZE, {}, 4))
        self.assertFalse(use_segments(1024, {"Accept-Ranges": "bytes"}, 4))


class SegmentedDownloadTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="iwaratool-segmented-")
        self.data = random.Random(7).randbytes(SIZE)
        self.mgr = DownloadManager()
        self.mgr._watchdog_stop.set()
        self.final = os.path.join(self._tmp, "video.mp4")
        self.temp = f"{self.final}_temp"
        self.task = DownloadTask("t", "", "t", status=TaskStatus.DOWNLOADING, file_id="file-a", quality="Source")
        self.task.file_path = self.final
        self.task.download_url = "https://example.invalid/file"
        self.mgr._tasks["t"] = self.task
        self.failures, self.completed = [], []
        self.mgr._fail_task = lambda task_id, reason: self.failures.append(reason)
        self.mgr._complete_task = lambda task_id: self.completed.append(task_id)
        patches = [
            mock.patch.object(transfer, "app_config", SimpleNamespace(native_download_connections=4)),
            mock.patch.object(segmented, "SEGMENT_RETRIES", 0),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def run_download(self, server):
        self.mgr.api.scraper.get = server.get
        self.mgr._download_task_native("t", final_path=self.final, temp_path=self.temp)

    def test_downloads_in_four_ranges(self):
        server = _Server(self.data)
        self.run_download(server)
        self.assertEqual(self.failures, [])
        self.assertEqual(self.completed, ["t"])
        with open(self.final, "rb") as handle:
            self.assertEqual(handle.read(), self.data)
        self.assertEqual(len([r for r in server.requests if r]), 4)
        self.assertFalse(os.path.exists(self.temp))
        self.assertEqual(read_resume_meta(self.temp), {})

    def test_server_without_ranges_uses_one_stream(self):
        server = _Server(self.data, ranges=False)
        self.run_download(server)
        self.assertEqual(self.completed, ["t"])
        self.assertEqual(server.requests, [""])
        with open(self.final, "rb") as handle:
            self.assertEqual(handle.read(), self.data)

    def test_a_dropped_segment_resumes_where_it_stopped(self):
        second = plan_segments(SIZE, 4)[1]
        self.run_download(_Server(self.data, broken_start=second.start, fail_after=512 * 1024))
        self.assertEqual(self.completed, [])
        self.assertEqual(len(self.failures), 1)
        meta = read_resume_meta(self.temp)
        self.assertEqual(meta["backend"], BACKEND_SEGMENTED)
        saved = segments_from_meta(meta, SIZE)
        self.assertGreater(saved[1].done, 0)
        self.assertLess(saved[1].done, saved[1].size)

        retry = _Server(self.data)
        self.run_download(retry)
        self.assertEqual(self.completed, ["t"])
        with open(self.final, "rb") as handle:
            self.assertEqual(handle.read(), self.data)
        # Only what was missing was asked for again, starting where each segment stopped.
        for spec in retry.requests:
            start = int(spec.removeprefix("bytes=").partition("-")[0])
            self.assertTrue(any(s.start + s.done == start and not s.finished for s in saved), spec)

    def test_single_stream_partial_files_stay_single_stream(self):
        with open(self.temp, "wb") as handle:
            handle.write(self.data[:1000])
        write_resume_meta(self.temp, "file-a:Source", SIZE, BACKEND_NATIVE)
        server = _Server(self.data)
        self.run_download(server)
        self.assertEqual(server.requests, ["bytes=1000-"])
        with open(self.final, "rb") as handle:
            self.assertEqual(handle.read(), self.data)

    def test_cancel_keeps_the_partial_file(self):
        cancelled = []
        self.mgr._cancel_task_terminal = lambda task_id, reason: cancelled.append(task_id)
        server = _Server(self.data)
        real_get = server.get

        def get(url, headers=None, **kwargs):
            if headers and headers.get("Range"):
                self.task.cancel_requested = True
            return real_get(url, headers=headers, **kwargs)

        self.mgr.api.scraper.get = get
        self.mgr._download_task_native("t", final_path=self.final, temp_path=self.temp)
        self.assertEqual(self.completed, [])
        self.assertEqual(cancelled, ["t"])
        self.assertTrue(os.path.exists(self.temp))
        self.assertEqual(read_resume_meta(self.temp)["backend"], BACKEND_SEGMENTED)


if __name__ == "__main__":
    unittest.main()
