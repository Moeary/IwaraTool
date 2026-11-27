"""The built-in downloader over several connections, each fetching its own byte range.

A video of ``MULTI_MIN_BYTES`` or more from a server that accepts ranges is
split into up to ``native_download_connections`` segments.  The partial file
is created at full size and each worker writes its segment in place, so a
paused or failed download keeps every byte received: the segment map (where
each segment has got to) is saved beside it in the resume record (backend
``native-segmented``) and the next attempt continues each segment from there.
A partial file written by the single-connection downloader, aria2 or another
file / quality is never continued this way (``partial_file_reusable``).

The connections share the global speed limit and the stall watchdog sees
every chunk.  A segment that drops is retried a few times before the task
fails; whatever it had written stays for the retry.
"""
from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass

from ...i18n import tr
from ...logging_setup import get_logger
from ...signal_bus import signal_bus
from .integrity import BACKEND_SEGMENTED, parse_content_range, read_resume_meta, write_resume_meta
from .speed_format import _fmt_bytes, _fmt_speed

logger = get_logger(__name__)

MULTI_MIN_BYTES = 8 * 1024 * 1024  # smaller files are not worth the extra connections
SEGMENT_MIN_BYTES = 2 * 1024 * 1024
MAX_CONNECTIONS = 16
CHUNK_BYTES = 256 * 1024
SEGMENT_RETRIES = 3
REPORT_SECONDS = 0.5
SAVE_SECONDS = 2.0


class SegmentError(Exception):
    """A segment could not be fetched (after its retries)."""


class RangeIgnored(SegmentError):
    """The server answered a range request with the whole file: ranges cannot be used."""


class ServerFileChanged(SegmentError):
    """The server's file is no longer the one the saved segments came from."""


@dataclass
class Segment:
    start: int
    end: int  # inclusive
    done: int = 0

    @property
    def size(self) -> int:
        return self.end - self.start + 1

    @property
    def finished(self) -> bool:
        return self.done >= self.size


def plan_segments(total: int, connections: int) -> list[Segment]:
    """Even segments of at least ``SEGMENT_MIN_BYTES``; one when the file is small."""
    count = max(1, min(int(connections), MAX_CONNECTIONS, total // SEGMENT_MIN_BYTES or 1))
    size = total // count
    segments = []
    for index in range(count):
        start = index * size
        end = total - 1 if index == count - 1 else start + size - 1
        segments.append(Segment(start, end))
    return segments


def segments_from_meta(meta: dict, total: int) -> list[Segment] | None:
    """The saved segment map when it covers ``total`` bytes exactly; else ``None``."""
    raw = meta.get("segments")
    if not isinstance(raw, list) or not raw or int(meta.get("total", 0) or 0) != total:
        return None
    segments: list[Segment] = []
    expected = 0
    for item in raw:
        try:
            start, end, done = (int(v) for v in item)
        except (TypeError, ValueError):
            return None
        if start != expected or end < start or not 0 <= done <= end - start + 1:
            return None
        segments.append(Segment(start, end, done))
        expected = end + 1
    return segments if expected == total else None


def save_segments(temp_path: str, source: str, total: int, segments: list[Segment]) -> None:
    write_resume_meta(temp_path, source, total, BACKEND_SEGMENTED,
                      segments=[[s.start, s.end, s.done] for s in segments])


def accepts_ranges(headers) -> bool:
    return "bytes" in str(headers.get("Accept-Ranges", "") or "").lower()


def use_segments(total: int, headers, connections: int) -> bool:
    return connections > 1 and total >= MULTI_MIN_BYTES and accepts_ranges(headers)


class SegmentedTransferMixin:
    """``_download_segmented``: the multi-connection transfer for ``TransferMixin``."""

    def _saved_segments(self, task, temp_path: str) -> tuple[int, list[Segment]] | None:
        """``(total, segments)`` of a segmented partial file this task may continue."""
        from .integrity import partial_file_reusable

        source = self._resume_source(task)
        if not os.path.exists(temp_path) or not partial_file_reusable(temp_path, source, BACKEND_SEGMENTED):
            return None
        meta = read_resume_meta(temp_path)
        total = int(meta.get("total", 0) or 0)
        segments = segments_from_meta(meta, total)
        if not segments or os.path.getsize(temp_path) != total:
            return None
        return total, segments

    def _download_segmented(self, task, task_id: str, temp_path: str, final_path: str, total: int,
                            segments: list[Segment] | None = None, connections: int = 4) -> None:
        source = self._resume_source(task)
        resumed = segments is not None
        segments = segments or plan_segments(total, connections)
        if not resumed:
            with open(temp_path, "wb") as handle:
                handle.truncate(total)  # full size up front: every worker writes in place
        save_segments(temp_path, source, total, segments)
        done_before = sum(s.done for s in segments)
        signal_bus.log_message.emit(tr(
            f"  {len(segments)} connections; " + (f"resuming at {_fmt_bytes(done_before)}" if resumed else "new download"),
            f"  {len(segments)} 路连接；" + (f"断点续传，已有 {_fmt_bytes(done_before)}" if resumed else "全新下载"),
            f"  {len(segments)} 本の接続；" + (f"レジューム（既存 {_fmt_bytes(done_before)}）" if resumed else "新規"),
        ))
        with self._lock:
            task.total_bytes = total
            task.downloaded_bytes = done_before
            self._touch_task_activity_locked(task_id)

        lock = threading.Lock()
        stop = threading.Event()
        errors: list[Exception] = []

        def cancelled() -> bool:
            return stop.is_set() or self._is_cancel_requested(task_id)

        def run(segment: Segment) -> None:
            try:
                self._fetch_segment(task, task_id, temp_path, segment, total, lock, cancelled)
            except Exception as exc:  # the first error stops the others; their bytes stay for the retry
                with lock:
                    errors.append(exc)
                stop.set()

        pending = [s for s in segments if not s.finished]
        last_report = last_save = time.monotonic()
        last_bytes = done_before
        with ThreadPoolExecutor(max_workers=max(1, len(pending)), thread_name_prefix=f"segment-{task_id[:8]}") as pool:
            futures = {pool.submit(run, s) for s in pending}
            while futures:
                _finished, futures = wait(futures, timeout=REPORT_SECONDS)
                now = time.monotonic()
                with lock:
                    downloaded = sum(s.done for s in segments)
                if self._is_cancel_requested(task_id):
                    stop.set()
                speed = (downloaded - last_bytes) / max(now - last_report, 1e-3)
                last_report, last_bytes = now, downloaded
                speed_str = _fmt_speed(speed)
                with self._lock:
                    task.downloaded_bytes = downloaded
                    task.speed_str = speed_str
                signal_bus.task_progress_updated.emit(task_id, downloaded, total, speed_str)
                if now - last_save >= SAVE_SECONDS:
                    with lock:
                        save_segments(temp_path, source, total, segments)
                    last_save = now
        with lock:
            save_segments(temp_path, source, total, segments)
            downloaded = sum(s.done for s in segments)
        signal_bus.task_progress_updated.emit(task_id, downloaded, total, "")

        if self._cancel_if_requested(task_id):
            return  # the partial file and its map stay for a resume
        if any(isinstance(e, RangeIgnored) for e in errors):
            # Ranges were advertised but not honoured: start over over one connection.
            signal_bus.log_message.emit(tr(
                "  The server ignored range requests; downloading over one connection",
                "  服务器未按分段返回数据，改用单连接下载",
                "  サーバーが範囲指定に従わないため、1 本の接続でダウンロードします",
            ))
            self._discard_temp_file(temp_path)
            self._download_task_native(task_id, final_path=final_path, temp_path=temp_path, connections=1)
            return
        changed = next((e for e in errors if isinstance(e, ServerFileChanged)), None)
        if changed is not None:
            self._discard_temp_file(temp_path)  # the saved parts belong to another file
            self._fail_task(task_id, tr(
                f"The file on the server changed ({changed}); the partial download was removed. Retry to download again.",
                f"服务器上的文件已变化（{changed}），已清除未完成的部分；请重试以重新下载。",
                f"サーバー上のファイルが変わりました（{changed}）。途中のデータを削除しました。再試行してください。",
            ))
            return
        if errors or not all(s.finished for s in segments):
            reason = str(errors[0]) if errors else ""
            self._fail_task(task_id, tr(
                f"Incomplete download ({reason or 'a connection stopped'}). Retry to resume.",
                f"下载不完整（{reason or '有连接中断'}），重试可继续。",
                f"ダウンロードが不完全です（{reason or '接続が途切れました'}）。再試行で再開できます。",
            ))
            return
        if not self._finalize_temp_file(task_id, temp_path=temp_path, final_path=final_path):
            return
        signal_bus.log_message.emit(tr(
            f"[Done] \"{task.title}\" total size {_fmt_bytes(total)}",
            f"[完成] 《{task.title}》 总大小 {_fmt_bytes(total)}",
            f"[完了] 「{task.title}」 合計サイズ {_fmt_bytes(total)}",
        ))
        self._complete_task(task_id)

    def _fetch_segment(self, task, task_id: str, temp_path: str, segment: Segment, total: int,
                       lock: threading.Lock, cancelled: Callable[[], bool]) -> None:
        attempt = 0
        # Unbuffered: a byte counts in ``segment.done`` (and so in the saved map) only once the OS has it.
        with open(temp_path, "r+b", buffering=0) as handle:
            while not segment.finished and not cancelled():
                start = segment.start + segment.done
                headers = {"Range": f"bytes={start}-{segment.end}"}
                token = self._current_token()
                if token:
                    headers["Authorization"] = f"Bearer {token}"
                try:
                    with self.api.scraper.get(task.download_url, headers=headers, stream=True, timeout=60,
                                              proxies=self._download_request_proxies()) as resp:
                        if resp.status_code == 200:
                            raise RangeIgnored("the server sent the whole file for a range request")
                        if resp.status_code != 206:
                            raise SegmentError(f"HTTP {resp.status_code} for a range request")
                        got_start, got_end, got_total = parse_content_range(resp.headers.get("Content-Range"))
                        if got_total is not None and got_total != total:
                            raise ServerFileChanged(f"the server's file is now {got_total} bytes, not {total}")
                        if got_start != start or got_end is None or got_end > segment.end:
                            raise SegmentError(f"asked for bytes {start}-{segment.end}, got {got_start}-{got_end}")
                        handle.seek(start)
                        for chunk in resp.iter_content(chunk_size=CHUNK_BYTES):
                            if cancelled():
                                return
                            if not chunk:
                                continue
                            room = segment.size - segment.done
                            chunk = chunk[:room]  # never past the segment, whatever the server sends
                            if not self._rate_limiter.throttle(len(chunk), cancelled=cancelled,
                                                               on_wait=lambda: self._touch_task_activity(task_id)):
                                return
                            view = memoryview(chunk)
                            while view:  # a raw write may take only part of the chunk
                                view = view[handle.write(view):]
                            with lock:
                                segment.done += len(chunk)
                            self._touch_task_activity(task_id)
                            if segment.finished:
                                break
                    attempt = 0  # progress made: a later drop gets its full retries again
                except SegmentError:
                    raise
                except Exception as exc:
                    attempt += 1
                    if attempt > SEGMENT_RETRIES:
                        raise SegmentError(str(exc)) from exc
                    logger.info("Segment %s-%s of %s dropped (%s); retrying", segment.start, segment.end, task_id, exc)
                    time.sleep(min(2 ** attempt, 8))
            handle.flush()
