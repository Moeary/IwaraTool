"""Scan a folder and match each video file to its Iwara video."""

from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

from ...i18n import tr
from ..api.client import IwaraAPI
from .matching import scan_video_files
from .file_identification import RepairFileIdentificationMixin


class RepairScanningMixin(RepairFileIdentificationMixin):
    """Scan a folder and match each video file to its Iwara video."""

    def scan_repair_folder(
        self,
        folder: str,
        *,
        filename_template: str = "",
        output_root: str = "",
        move_to_output: bool = False,
        max_workers: int = 6,
        progress_callback: Callable[[dict[str, Any], int, int], Any] | None = None,
        activity_callback: Callable[[int, int, int], Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Scan a local folder and resolve files to Iwara metadata.

        The scan is read-only. It first uses durable local history/subscription
        metadata and explicit IDs in filenames, then falls back to a bounded
        Iwara title search. Metadata lookups use one API session per worker so
        network-bound files can resolve concurrently without sharing a
        ``cloudscraper`` session. Unmatched or ambiguous files are returned to
        the UI instead of being modified.
        """

        root = os.path.abspath(os.path.expanduser(str(folder or "").strip()))
        files = scan_video_files(root)
        if not os.path.isdir(root):
            return []
        destination_root = os.path.abspath(
            os.path.expanduser(str(output_root or root).strip())
        )
        history_by_path, local_candidates, known_ids = self._repair_scan_context()

        total = len(files)
        if not total:
            return []

        try:
            worker_count = max(1, min(8, int(max_workers)))
        except (TypeError, ValueError):
            worker_count = 6

        scan_clients: list[IwaraAPI] = []
        scan_one = self._repair_scan_one_factory(
            total,
            activity_callback,
            history_by_path,
            {
                "local_candidates": local_candidates,
                "known_ids": known_ids,
                "filename_template": filename_template,
                "output_root": destination_root,
                "move_to_output": move_to_output,
                "api_call": self._repair_scan_api(scan_clients),
            },
        )
        result = self._run_repair_scan(files, scan_one, worker_count, progress_callback, scan_clients)
        result.sort(key=lambda item: str(item.get("path", "") or "").casefold())
        return self.preview_repair_files(
            result,
            options={
                "filename_template": filename_template,
                "output_root": destination_root,
                "move_to_output": move_to_output,
            },
        )

    def _repair_scan_context(self):
        """``(history record by path, local candidates, known video ids)`` for a scan."""

        history_records = self.history.list_records(include_raw=True)
        history_by_path = {
            self._repair_normalize_path(record.get("file_path", "")): record
            for record in history_records
            if str(record.get("file_path", "") or "").strip()
        }
        subscription_items = self.subscriptions.list_items()
        local_candidates = self._repair_local_candidates(history_records, subscription_items)
        known_ids = {
            str(candidate.get("video_id", "") or "").strip()
            for candidate in local_candidates
            if str(candidate.get("video_id", "") or "").strip()
        }
        return history_by_path, local_candidates, known_ids

    def _repair_scan_api(self, scan_clients: list[IwaraAPI]):
        """An API caller giving each scan thread its own session (collected in ``scan_clients``)."""

        # The normal manager API is serialized because the shared scraper is
        # also used by downloads and login. Repair scanning is read-only, so it
        # gets short-lived per-thread sessions instead of contending on that
        # lock. Login/proxy settings are snapshotted for the duration of scan.
        scan_token = getattr(self.api, "token", None)
        scan_proxies = dict(getattr(getattr(self.api, "scraper", None), "proxies", {}) or {})
        scan_api_state = threading.local()

        def scan_api_call(method_name: str, *args, **kwargs):
            client = getattr(scan_api_state, "client", None)
            if client is None:
                try:
                    client = IwaraAPI()
                    client.token = scan_token
                    client.scraper.proxies = dict(scan_proxies)
                    scan_clients.append(client)
                except Exception:
                    # Keep scanning usable in reduced/test environments where
                    # a second scraper cannot be initialized.
                    client = False
                scan_api_state.client = client
            if client is False:
                return self._api_call(method_name, *args, **kwargs)
            return getattr(client, method_name)(*args, **kwargs)

        return scan_api_call

    def _repair_scan_one_factory(self, total: int, activity_callback, history_by_path, scan_kwargs: dict[str, Any]):
        """The per-file scan function, reporting started / finished counts as it goes."""

        activity_lock = threading.Lock()
        started_count = 0
        completed_count = 0

        def report_activity(completed: int, active: int):
            self._repair_report_activity(
                activity_callback,
                completed,
                total,
                active,
            )

        def scan_one(path: str) -> dict[str, Any]:
            nonlocal started_count, completed_count
            with activity_lock:
                started_count += 1
                completed = completed_count
                active = started_count - completed_count
            report_activity(completed, active)
            try:
                return self._scan_repair_file(
                    path,
                    record=history_by_path.get(self._repair_normalize_path(path)),
                    **scan_kwargs,
                )
            finally:
                with activity_lock:
                    completed_count += 1
                    completed = completed_count
                    active = started_count - completed_count
                report_activity(completed, active)

        return scan_one

    def _run_repair_scan(
        self, files: list[str], scan_one, worker_count: int, progress_callback, scan_clients: list[IwaraAPI],
    ) -> list[dict[str, Any]]:
        total = len(files)
        result: list[dict[str, Any]] = []
        executor = ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix="iwara-repair-scan",
        )
        futures = {
            executor.submit(scan_one, path): path
            for path in files
        }
        interrupted = False
        try:
            for index, future in enumerate(as_completed(futures), start=1):
                path = futures[future]
                try:
                    item = future.result()
                except Exception as exc:
                    item = self._repair_scan_error_item(path, exc)
                result.append(item)
                if not self._repair_report_progress(progress_callback, item, index, total):
                    interrupted = True
                    break
        finally:
            executor.shutdown(wait=True, cancel_futures=interrupted)
            for client in scan_clients:
                try:
                    client.scraper.close()
                except Exception:
                    pass
        return result

    @staticmethod
    def _repair_report_progress(callback, item: dict[str, Any], index: int, total: int) -> bool:
        if callback is None:
            return True
        value = callback(item, index, total)
        return value is not False

    @staticmethod
    def _repair_report_activity(
        callback,
        completed: int,
        total: int,
        active: int,
    ):
        if callback is None:
            return
        try:
            callback(completed, total, active)
        except Exception:
            # Progress feedback must never interrupt a scan worker.
            return

    @staticmethod
    def _repair_scan_error_item(path: str, error: Exception) -> dict[str, Any]:
        return {
            "path": path,
            "original_name": os.path.basename(path),
            "video_id": "",
            "title": "",
            "author": "",
            "published_at": "",
            "target_name": "",
            "target_relative_path": "",
            "target_path": "",
            "status": "failed",
            "source": "",
            "message": tr(
                f"Scan failed: {error}",
                f"扫描失败：{error}",
                f"検索に失敗しました: {error}",
            ),
            "metadata": {},
            "cached_meta": {},
            "existing_history": False,
        }

    @staticmethod
    def _repair_local_candidates(
        history_records: list[dict[str, Any]],
        subscription_items: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        candidates: dict[str, dict[str, Any]] = {}
        for source in (*history_records, *subscription_items):
            video_id = str(source.get("video_id", "") or source.get("id", "") or "").strip()
            if not video_id:
                continue
            key = video_id.casefold()
            candidate = dict(source)
            candidate["video_id"] = video_id
            candidate.setdefault("source_url", f"https://www.iwara.tv/video/{video_id}")
            candidates.setdefault(key, candidate)
        return list(candidates.values())
