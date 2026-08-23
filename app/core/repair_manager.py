"""Folder repair operations mixed into the central download manager."""
from __future__ import annotations

import os
import re
import shutil
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

from ..i18n import tr
from ..signal_bus import signal_bus
from .api import IwaraAPI
from .models import DownloadTask
from .repair import (
    candidate_match_score,
    extract_iwara_video_id,
    filename_search_text,
    format_repair_filename,
    guess_filename_video_id,
    scan_video_files,
)
from .task_metadata import _dict_or_empty


class RepairManagerMixin:
    def scan_repair_folder(
        self,
        folder: str,
        *,
        filename_template: str = "",
        output_root: str = "",
        move_to_output: bool = True,
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

        total = len(files)
        if not total:
            return []

        try:
            worker_count = max(1, min(8, int(max_workers)))
        except (TypeError, ValueError):
            worker_count = 6

        # The normal manager API is serialized because the shared scraper is
        # also used by downloads and login. Repair scanning is read-only, so it
        # gets short-lived per-thread sessions instead of contending on that
        # lock. Login/proxy settings are snapshotted for the duration of scan.
        scan_token = getattr(self.api, "token", None)
        scan_proxies = dict(getattr(getattr(self.api, "scraper", None), "proxies", {}) or {})
        scan_api_state = threading.local()
        scan_clients: list[IwaraAPI] = []

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
                    local_candidates=local_candidates,
                    known_ids=known_ids,
                    filename_template=filename_template,
                    output_root=destination_root,
                    move_to_output=move_to_output,
                    api_call=scan_api_call,
                )
            finally:
                with activity_lock:
                    completed_count += 1
                    completed = completed_count
                    active = started_count - completed_count
                report_activity(completed, active)

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

        result.sort(key=lambda item: str(item.get("path", "") or "").casefold())
        return result

    def repair_folder_files(
        self,
        items: list[dict[str, Any]],
        *,
        options: dict[str, Any] | None = None,
        progress_callback: Callable[[dict[str, Any], int, int], Any] | None = None,
    ) -> dict[str, Any]:
        """Rename and enrich a previously scanned set of local video files.

        Repair never queues a download for a file that is already present. The
        ``download_video`` option is retained for a future ID/URL import path,
        but a folder repair only operates on existing files by design.
        """

        normalized_options = {
            "filename_template": str((options or {}).get("filename_template", "") or ""),
            "output_root": self._repair_normalize_path((options or {}).get("output_root", "")),
            "move_to_output": bool((options or {}).get("move_to_output", True)),
            "download_video": bool((options or {}).get("download_video", False)),
            "download_thumbnail": bool((options or {}).get("download_thumbnail", False)),
            "collect_nfo": bool((options or {}).get("collect_nfo", False)),
            "add_to_history": bool((options or {}).get("add_to_history", True)),
            "rename": bool((options or {}).get("rename", True)),
        }
        results: list[dict[str, Any]] = []
        counts = {
            "total": len(items),
            "renamed": 0,
            "unchanged": 0,
            "thumbnail": 0,
            "nfo": 0,
            "history": 0,
            "skipped": 0,
            "failed": 0,
        }
        for index, item in enumerate(items, start=1):
            result = self._repair_one_file(item, normalized_options)
            results.append(result)
            state = str(result.get("status", "") or "")
            for key in ("renamed", "unchanged", "thumbnail", "nfo", "history"):
                if result.get(key):
                    counts[key] += 1
            if state in {"not_found", "ambiguous", "skipped"}:
                counts["skipped"] += 1
            elif state == "failed":
                counts["failed"] += 1
            if not self._repair_report_progress(progress_callback, result, index, len(items)):
                break
        counts["processed"] = len(results)
        counts["interrupted"] = len(results) < len(items)
        counts["items"] = results
        return counts

    @staticmethod
    def _repair_normalize_path(path: Any) -> str:
        raw = str(path or "").strip()
        if not raw:
            return ""
        return os.path.normcase(os.path.abspath(os.path.expanduser(raw)))

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

    def _scan_repair_file(
        self,
        path: str,
        *,
        record: dict[str, Any] | None,
        local_candidates: list[dict[str, Any]],
        known_ids: set[str],
        filename_template: str,
        output_root: str,
        move_to_output: bool,
        api_call: Callable[..., Any] | None = None,
    ) -> dict[str, Any]:
        api_call = api_call or self._api_call
        item: dict[str, Any] = {
            "path": path,
            "original_name": os.path.basename(path),
            "video_id": "",
            "title": "",
            "author": "",
            "published_at": "",
            "target_name": "",
            "target_relative_path": "",
            "target_path": "",
            "status": "not_found",
            "source": "",
            "message": "",
            "metadata": {},
            "cached_meta": dict(record or {}),
            "existing_history": bool(record),
            "output_root": output_root,
            "move_to_output": bool(move_to_output),
        }
        stem = os.path.splitext(os.path.basename(path))[0]
        video_id = extract_iwara_video_id(stem)
        source = "filename-id" if video_id else ""

        if not video_id and record:
            video_id = str(record.get("video_id", "") or "").strip()
            source = "history-path" if video_id else ""

        if not video_id:
            matching_ids = [
                candidate_id
                for candidate_id in known_ids
                if self._repair_id_in_stem(candidate_id, stem)
            ]
            if len(matching_ids) == 1:
                video_id = matching_ids[0]
                source = "known-id"
            elif len(matching_ids) > 1:
                item.update(
                    status="ambiguous",
                    message=tr(
                        "Several known Iwara IDs appear in this filename",
                        "文件名中出现了多个已知 Iwara ID",
                        "ファイル名に複数の既知 Iwara ID が含まれています",
                    ),
                )
                return item

        local_candidate: dict[str, Any] | None = None
        if not video_id:
            local_candidate, ambiguous = self._select_repair_candidate(path, local_candidates)
            if ambiguous:
                item.update(
                    status="ambiguous",
                    message=tr(
                        "The filename matches multiple local subscription/history records",
                        "文件名匹配到多个本地订阅/历史记录",
                        "ファイル名が複数のローカル購読/履歴に一致します",
                    ),
                )
                return item
            if local_candidate:
                video_id = str(local_candidate.get("video_id", "") or "").strip()
                source = "local-metadata"

        if not video_id:
            filename_id = guess_filename_video_id(stem)
            if filename_id:
                video_id = filename_id
                source = "filename-token"

        video_info: dict[str, Any] | None = None
        error = ""
        if video_id:
            video_info, error = api_call("get_video_info", video_id)
            if not video_info and source == "filename-token":
                # A title can end in an eight-character word. Treat the token
                # as a hint only and fall back to a title search when the API
                # rejects it.
                video_id = ""
                source = ""
        if not video_id:
            query = filename_search_text(path).strip()
            if query:
                search_items, error = api_call(
                    "get_videos_by_query",
                    {"q": query, "sort": "date", "limit": "30"},
                    max_pages=1,
                    max_results=30,
                )
                local_candidate, ambiguous = self._select_repair_candidate(path, search_items or [])
                if ambiguous:
                    item.update(
                        status="ambiguous",
                        message=tr(
                            "Iwara search returned several equally likely videos",
                            "Iwara 搜索返回了多个同样可能的视频",
                            "Iwara検索で同程度に一致する動画が複数あります",
                        ),
                    )
                    return item
                if local_candidate:
                    video_id = str(local_candidate.get("video_id", "") or local_candidate.get("id", "")).strip()
                    source = "iwara-search"
                    if video_id:
                        video_info, detail_error = api_call("get_video_info", video_id)
                        error = detail_error or error

        cached = record or local_candidate or {}
        if not video_info and not video_id:
            item["message"] = error or tr(
                "No matching Iwara video was found",
                "没有找到匹配的 Iwara 视频",
                "一致する Iwara 動画が見つかりません",
            )
            return item
        if not video_id:
            video_id = str(video_info.get("id", "") or video_info.get("video_id", "") or "").strip() if video_info else ""
        if not video_info and not cached:
            item["video_id"] = video_id
            item["message"] = error or tr(
                "Video metadata is unavailable; skipped",
                "视频元数据不可用，已跳过",
                "動画メタデータを取得できないためスキップしました",
            )
            return item

        metadata_source = video_info or cached
        meta = self._history_meta_from_item(metadata_source, record)
        meta["video_id"] = video_id
        meta["source_url"] = str(
            metadata_source.get("source_url", "")
            or f"https://www.iwara.tv/video/{video_id}"
        )
        user = _dict_or_empty(metadata_source.get("user"))
        author = str(meta.get("author", "") or user.get("username", "") or "").strip()
        title = str(meta.get("title", "") or video_id).strip()
        target_name = format_repair_filename(filename_template, meta, path)
        if not move_to_output:
            target_name = os.path.basename(target_name)
        target_root = output_root if move_to_output else os.path.dirname(path)
        target_path = os.path.join(target_root, target_name)
        item.update(
            video_id=video_id,
            title=title,
            author=author,
            published_at=str(meta.get("published_at", "") or ""),
            target_name=target_name,
            target_relative_path=target_name,
            target_path=target_path,
            status="ready",
            source=source or "cached-metadata",
            message=(
                tr("Name is already up to date", "文件名已经符合规则", "ファイル名は既に規則どおりです")
                if self._repair_normalize_path(path) == self._repair_normalize_path(target_path)
                else tr("Ready to repair", "可修复", "修復可能")
            ),
            metadata=dict(video_info or {}),
            cached_meta=meta,
            existing_history=bool(record),
        )
        if not video_info and error:
            item["message"] = tr(
                f"Using cached metadata; live lookup failed: {error}",
                f"实时查询失败，使用缓存元数据：{error}",
                f"ライブ取得失敗、キャッシュメタデータを使用: {error}",
            )
        return item

    @staticmethod
    def _repair_id_in_stem(video_id: str, stem: str) -> bool:
        video_id = str(video_id or "").strip()
        stem = str(stem or "")
        if not video_id:
            return False
        pattern = rf"(?<![A-Za-z0-9_-]){re.escape(video_id)}(?![A-Za-z0-9_-])"
        return bool(re.search(pattern, stem, re.IGNORECASE))

    @staticmethod
    def _select_repair_candidate(
        path: str,
        candidates: list[dict[str, Any]],
    ) -> tuple[dict[str, Any] | None, bool]:
        author_hint = os.path.basename(os.path.dirname(path))
        ranked: list[tuple[float, dict[str, Any]]] = []
        seen: set[str] = set()
        for candidate in candidates:
            video_id = str(candidate.get("video_id", "") or candidate.get("id", "")).strip()
            if not video_id or video_id.casefold() in seen:
                continue
            seen.add(video_id.casefold())
            score = candidate_match_score(path, candidate, author_hint=author_hint)
            if score > 0:
                ranked.append((score, candidate))
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        if not ranked:
            return None, False

        top_score, top = ranked[0]
        second_score = ranked[1][0] if len(ranked) > 1 else 0.0
        exact = top_score >= 1.95
        if exact and (len(ranked) == 1 or top_score - second_score >= 0.12):
            return top, False
        if top_score >= 1.15 and (len(ranked) == 1 or top_score - second_score >= 0.2):
            return top, False
        return None, top_score >= 0.9

    def _repair_one_file(
        self,
        item: dict[str, Any],
        options: dict[str, Any],
    ) -> dict[str, Any]:
        result = dict(item)
        path = str(item.get("path", "") or "")
        if str(item.get("status", "") or "") != "ready":
            result["status"] = str(item.get("status", "skipped") or "skipped")
            result["skipped"] = True
            return result
        if not path or not os.path.isfile(path):
            result.update(
                status="failed",
                message=tr("Source video no longer exists", "源视频已不存在", "元動画が見つかりません"),
            )
            return result

        video_id = str(item.get("video_id", "") or "").strip()
        source_url = str(item.get("cached_meta", {}).get("source_url", "") or f"https://www.iwara.tv/video/{video_id}")
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        cached_meta = item.get("cached_meta") if isinstance(item.get("cached_meta"), dict) else {}
        task = self._repair_task(video_id, metadata, cached_meta, source_url, path)
        target_name = format_repair_filename(options.get("filename_template", ""), cached_meta or metadata, path)
        move_to_output = bool(options.get("move_to_output", True))
        if not move_to_output:
            target_name = os.path.basename(target_name)
        output_root = str(options.get("output_root", "") or "")
        target_root = output_root if move_to_output and output_root else os.path.dirname(path)
        target_path = os.path.join(target_root, target_name)
        result.update(
            target_name=target_name,
            target_path=target_path,
            target_relative_path=target_name,
            output_root=target_root,
            move_to_output=move_to_output,
        )

        if options.get("rename", True):
            thumbnail_before = str(cached_meta.get("thumbnail_path", "") or "")
            ok, new_thumbnail, rename_message = self._rename_repair_file(
                path,
                target_path,
                thumbnail_before,
            )
            if not ok:
                result.update(status="failed", message=rename_message)
                return result
            if os.path.abspath(path).casefold() != os.path.abspath(target_path).casefold():
                result["renamed"] = True
            else:
                result["unchanged"] = True
            task.file_path = target_path
            task.filename = os.path.basename(target_path)
            if new_thumbnail:
                task.thumbnail_path = new_thumbnail
        else:
            task.file_path = path
            task.filename = os.path.basename(path)

        # A folder repair is metadata-only for the media file itself. Existing
        # files are never sent through the download scheduler.
        if options.get("download_video"):
            signal_bus.log_message.emit(
                tr(
                    f"[Repair] existing video kept; download skipped: {path}",
                    f"[修复] 已保留本地视频，不重新下载：{path}",
                    f"[修復] 既存動画を保持し、再ダウンロードをスキップ: {path}",
                )
            )

        if options.get("download_thumbnail"):
            result["thumbnail"] = self._download_thumbnail(task, require_video_file=True)
        if options.get("collect_nfo"):
            result["nfo"] = self._write_nfo(task, require_video_file=True)

        existing = self.history.get_record(video_id, include_raw=True)
        if options.get("add_to_history"):
            history_item = dict(metadata or cached_meta)
            history_item["video_id"] = video_id
            history_item["source_url"] = source_url
            history_meta = self._history_meta_from_item(history_item, existing)
            history_meta["file_path"] = task.file_path
            history_meta["thumbnail_path"] = task.thumbnail_path or str(
                history_meta.get("thumbnail_path", "") or ""
            )
            self.history.upsert_downloaded(history_meta)
            result["history"] = True
            self.subscriptions.mark_items_seen([video_id])
        elif existing:
            # Do not add a new history row when disabled, but keep an existing
            # row's path synchronized after a rename.
            self.history.update_file_paths(
                video_id,
                file_path=task.file_path,
                thumbnail_path=task.thumbnail_path or str(existing.get("thumbnail_path", "") or ""),
            )

        result["status"] = "completed"
        result["message"] = tr(
            "Repair completed",
            "修复完成",
            "修復完了",
        )
        signal_bus.log_message.emit(
            tr(
                f"[Repair] completed: {path} -> {task.file_path}",
                f"[修复] 已完成：{path} → {task.file_path}",
                f"[修復] 完了: {path} -> {task.file_path}",
            )
        )
        return result

    def _repair_task(
        self,
        video_id: str,
        metadata: dict[str, Any],
        cached_meta: dict[str, Any],
        source_url: str,
        path: str,
    ) -> DownloadTask:
        if metadata:
            task = self._metadata_task_from_video_info(video_id, metadata, source_url)
            task.quality = str(cached_meta.get("quality", "") or task.quality or "metadata")
        else:
            task = DownloadTask(str(uuid.uuid4()), source_url, video_id)
            source = cached_meta
            file_info = _dict_or_empty(source.get("file"))
            self._apply_task_metadata(
                task,
                title=str(source.get("title", "") or video_id),
                author=str(source.get("author", "") or source.get("username", "") or ""),
                published_at=str(source.get("published_at", "") or source.get("createdAt", "") or ""),
                likes=int(source.get("likes", source.get("numLikes", 0)) or 0),
                views=int(source.get("views", source.get("numViews", 0)) or 0),
                slug=str(source.get("slug", "") or ""),
                rating=str(source.get("rating", "") or ""),
                duration=int(source.get("duration", file_info.get("duration", 0)) or 0),
                comments=int(source.get("comments", source.get("numComments", 0)) or 0),
                tags_json=str(source.get("tags_json", "") or ""),
                raw_json=str(source.get("raw_json", "") or ""),
                file_url=str(source.get("file_url", "") or ""),
                file_id=str(source.get("file_id", "") or file_info.get("id", "") or ""),
                thumbnail_index=int(source.get("thumbnail_index", source.get("thumbnail", 0)) or 0),
            )
            task.quality = str(source.get("quality", "") or "metadata")
        task.file_path = path
        task.filename = os.path.basename(path)
        return task

    def _rename_repair_file(
        self,
        old_path: str,
        new_path: str,
        thumbnail_path: str = "",
    ) -> tuple[bool, str, str]:
        old_abs = os.path.abspath(old_path)
        new_abs = os.path.abspath(new_path)
        if old_abs.casefold() == new_abs.casefold():
            existing_thumb = thumbnail_path if os.path.isfile(thumbnail_path) else ""
            if not existing_thumb:
                existing_thumb = next(
                    (
                        f"{os.path.splitext(old_path)[0]}{extension}"
                        for extension in (".jpg", ".jpeg", ".png", ".webp")
                        if os.path.isfile(f"{os.path.splitext(old_path)[0]}{extension}")
                    ),
                    "",
                )
            return True, existing_thumb, ""
        if os.path.exists(new_path):
            return False, "", tr(
                f"Target file already exists: {new_path}",
                f"目标文件已存在：{new_path}",
                f"変更先ファイルは既に存在します: {new_path}",
            )

        old_stem = os.path.splitext(old_path)[0]
        new_stem = os.path.splitext(new_path)[0]
        sidecars: list[tuple[str, str]] = []
        seen_sources: set[str] = set()
        thumbnail_candidates = [thumbnail_path] if thumbnail_path else []
        thumbnail_candidates.extend(
            f"{old_stem}{extension}" for extension in (".jpg", ".jpeg", ".png", ".webp")
        )
        for source in thumbnail_candidates:
            source = str(source or "")
            source_key = self._repair_normalize_path(source)
            if not source_key or source_key in seen_sources or not os.path.isfile(source):
                continue
            seen_sources.add(source_key)
            sidecars.append((source, f"{new_stem}{os.path.splitext(source)[1] or '.jpg'}"))
        nfo_source = f"{old_stem}.nfo"
        if os.path.isfile(nfo_source) and self._repair_normalize_path(nfo_source) not in seen_sources:
            sidecars.append((nfo_source, f"{new_stem}.nfo"))

        for _source, target in sidecars:
            if os.path.exists(target):
                return False, "", tr(
                    f"Sidecar target already exists: {target}",
                    f"附属文件目标已存在：{target}",
                    f"関連ファイルの変更先が既に存在します: {target}",
                )

        renamed: list[tuple[str, str]] = []
        try:
            os.makedirs(os.path.dirname(new_abs), exist_ok=True)
            shutil.move(old_path, new_path)
            renamed.append((old_path, new_path))
            for source, target in sidecars:
                os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
                shutil.move(source, target)
                renamed.append((source, target))
        except Exception as exc:
            for source, target in reversed(renamed):
                try:
                    if os.path.exists(target) and not os.path.exists(source):
                        os.makedirs(os.path.dirname(os.path.abspath(source)), exist_ok=True)
                        shutil.move(target, source)
                except OSError:
                    pass
            return False, "", str(exc)

        new_thumbnail = ""
        for source, target in sidecars:
            if self._repair_normalize_path(source) in {
                self._repair_normalize_path(thumbnail_path),
                self._repair_normalize_path(f"{old_stem}.jpg"),
                self._repair_normalize_path(f"{old_stem}.jpeg"),
                self._repair_normalize_path(f"{old_stem}.png"),
                self._repair_normalize_path(f"{old_stem}.webp"),
            }:
                new_thumbnail = target
                break
        return True, new_thumbnail, ""

