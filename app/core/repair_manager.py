"""Folder repair operations mixed into the central download manager."""
from __future__ import annotations

import errno
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
    guess_filename_video_id,
    repair_target_path,
    scan_video_files,
)
from .task_metadata import _author_fields_from_user, _dict_or_empty


class RepairManagerMixin:
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
        return self.preview_repair_files(
            result,
            options={
                "filename_template": filename_template,
                "output_root": destination_root,
                "move_to_output": move_to_output,
            },
        )

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

        normalized_options = self._repair_options(options)
        # Compute and validate every destination before moving the first file.
        # Conflicting items remain untouched while unrelated items can finish.
        planned_items = self.preview_repair_files(items, options=normalized_options)
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
        for index, item in enumerate(planned_items, start=1):
            if item.get("target_conflict"):
                result = dict(item, status="failed", message=item["target_conflict"])
            else:
                try:
                    result = self._repair_one_file(item, normalized_options)
                except Exception as exc:
                    result = dict(item, status="failed", message=tr(
                        f"Repair failed: {exc}",
                        f"修复失败：{exc}",
                        f"修復に失敗しました: {exc}",
                    ))
            if result.get("status") == "failed":
                signal_bus.log_message.emit(tr(
                    f"[Repair] failed: {item.get('path', '')} -> {item.get('target_path', '')}: {result.get('message', '')}",
                    f"[修复] 失败：{item.get('path', '')} → {item.get('target_path', '')}：{result.get('message', '')}",
                    f"[修復] 失敗: {item.get('path', '')} -> {item.get('target_path', '')}: {result.get('message', '')}",
                ))
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

    def preview_repair_files(
        self,
        items: list[dict[str, Any]],
        *,
        options: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Plan video/sidecar targets without writing files or resolving metadata."""

        normalized = self._repair_options(options)
        result = [dict(item) for item in items]
        target_claims: dict[str, list[tuple[int, str, str]]] = {}
        source_claims: dict[str, set[int]] = {}
        conflicts: dict[int, list[str]] = {}

        def add_conflict(index: int, message: str):
            messages = conflicts.setdefault(index, [])
            if message not in messages:
                messages.append(message)

        for index, item in enumerate(result):
            if item.get("status") != "ready":
                continue
            item["target_conflict"] = ""
            path = str(item.get("path", "") or "")
            try:
                item.update(self._repair_target_fields(item, normalized))
                target_path = str(item["target_path"])
                cached = _dict_or_empty(item.get("cached_meta"))
                moves = self._repair_file_moves(
                    path, target_path, str(cached.get("thumbnail_path", "") or ""),
                ) if normalized["rename"] else [(path, path)]
                claims = list(moves)
                claimed_targets = {self._repair_normalize_path(target) for _, target in claims}
                # Two formats of the same basename can have different video
                # destinations but still collide on a newly generated sidecar.
                for option, extension in (("download_thumbnail", ".jpg"), ("collect_nfo", ".nfo")):
                    if not normalized[option]:
                        continue
                    target = f"{os.path.splitext(target_path)[0]}{extension}"
                    target_key = self._repair_normalize_path(target)
                    if target_key not in claimed_targets:
                        source = f"{os.path.splitext(path)[0]}{extension}"
                        claims.append((source, target))
                        claimed_targets.add(target_key)

                for source, target in claims:
                    source_key = self._repair_normalize_path(source)
                    target_key = self._repair_normalize_path(target)
                    target_claims.setdefault(target_key, []).append((index, source_key, target))
                    if os.path.isfile(source):
                        source_claims.setdefault(source_key, set()).add(index)
                    if source_key != target_key and os.path.lexists(target):
                        add_conflict(index, tr(
                            f"Target already exists: {target}",
                            f"目标已存在：{target}",
                            f"変更先は既に存在します: {target}",
                        ))
            except Exception as exc:
                add_conflict(index, tr(
                    f"Cannot plan repair: {exc}",
                    f"无法预览修复目标：{exc}",
                    f"修復先を準備できません: {exc}",
                ))

        for claims in target_claims.values():
            owners = {(index, source) for index, source, _ in claims}
            if len(owners) <= 1:
                continue
            target = claims[0][2]
            message = tr(
                f"Several files would use the same target: {target}",
                f"多个文件将使用同一目标：{target}",
                f"複数のファイルが同じ変更先を使用します: {target}",
            )
            for index, _, _ in claims:
                add_conflict(index, message)
        for source, owners in source_claims.items():
            if len(owners) <= 1:
                continue
            message = tr(
                f"The same source belongs to several repair items: {source}",
                f"同一源文件属于多个修复项目：{source}",
                f"同じ元ファイルが複数の修復項目に含まれています: {source}",
            )
            for index in owners:
                add_conflict(index, message)
        for index, messages in conflicts.items():
            result[index]["target_conflict"] = "\n".join(messages)
        return result

    def _repair_options(self, options: dict[str, Any] | None) -> dict[str, Any]:
        values = options or {}
        output_root = str(values.get("output_root", "") or "").strip()
        return {
            "filename_template": str(values.get("filename_template", "") or ""),
            "output_root": os.path.abspath(os.path.expanduser(output_root)) if output_root else "",
            "move_to_output": bool(values.get("move_to_output", False)),
            "download_video": bool(values.get("download_video", False)),
            "download_thumbnail": bool(values.get("download_thumbnail", False)),
            "collect_nfo": bool(values.get("collect_nfo", False)),
            "add_to_history": bool(values.get("add_to_history", True)),
            "rename": bool(values.get("rename", True)),
        }

    @staticmethod
    def _repair_target_fields(item: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
        path = str(item.get("path", "") or "")
        moving = bool(options.get("move_to_output", False))
        output_root = str(options.get("output_root", "") or "")
        if moving and not output_root and item.get("move_to_output"):
            # A scan with a blank organize output uses its selected source
            # root, even when the videos lie several directories below it.
            output_root = str(item.get("output_root", "") or "")
        if options.get("rename", True):
            cached = _dict_or_empty(item.get("cached_meta"))
            metadata = _dict_or_empty(item.get("metadata"))
            target_name, target_path = repair_target_path(
                options.get("filename_template", ""), cached or metadata, path,
                output_root=output_root,
                move_to_output=moving,
            )
        else:
            target_name, target_path = os.path.basename(path), os.path.abspath(path)
        return {
            "target_name": target_name,
            "target_relative_path": target_name,
            "target_path": target_path,
            "output_root": output_root if moving and options.get("rename", True) and output_root else os.path.dirname(os.path.abspath(path)),
            "move_to_output": moving,
        }

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
        target_name, target_path = repair_target_path(
            filename_template, meta, path,
            output_root=output_root,
            move_to_output=move_to_output,
        )
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
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        cached_meta = item.get("cached_meta") if isinstance(item.get("cached_meta"), dict) else {}
        source_url = str(cached_meta.get("source_url", "") or f"https://www.iwara.tv/video/{video_id}")
        task = self._repair_task(video_id, metadata, cached_meta, source_url, path)
        # Use the batch plan, including the exact paths shown by the preview.
        target_path = str(item.get("target_path", "") or path)

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
            if self._repair_normalize_path(path) != self._repair_normalize_path(target_path):
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
        result["file_path"] = task.file_path

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

        try:
            if options.get("download_thumbnail"):
                thumbnail_path = f"{os.path.splitext(task.file_path)[0]}.jpg"
                if os.path.lexists(thumbnail_path):
                    result["thumbnail"] = os.path.isfile(thumbnail_path) and os.path.getsize(thumbnail_path) > 0
                    if result["thumbnail"]:
                        task.thumbnail_path = thumbnail_path
                else:
                    result["thumbnail"] = self._download_thumbnail(task, require_video_file=True)
            if options.get("collect_nfo"):
                nfo_path = f"{os.path.splitext(task.file_path)[0]}.nfo"
                # An attached NFO is retained when following a renamed video.
                # Only missing sidecars are generated during folder repair.
                if os.path.lexists(nfo_path):
                    result["nfo"] = os.path.isfile(nfo_path) and os.path.getsize(nfo_path) > 0
                else:
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
                # Keep an existing history row synchronized after a rename.
                self.history.update_file_paths(
                    video_id,
                    file_path=task.file_path,
                    thumbnail_path=task.thumbnail_path or str(existing.get("thumbnail_path", "") or ""),
                )
        except Exception as exc:
            result.update(status="failed", message=tr(
                f"Metadata repair failed for {task.file_path}: {exc}",
                f"元数据修复失败，当前文件位于 {task.file_path}：{exc}",
                f"メタデータの修復に失敗しました（現在のファイル: {task.file_path}）: {exc}",
            ))
            return result

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
            source_author, source_username = _author_fields_from_user(source.get("user"))
            self._apply_task_metadata(
                task,
                title=str(source.get("title", "") or video_id),
                author=str(source.get("author", "") or source_author or source.get("username", "") or ""),
                username=str(source.get("username", "") or source_username or source.get("author", "") or ""),
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

    def _repair_file_moves(
        self,
        old_path: str,
        new_path: str,
        thumbnail_path: str = "",
    ) -> list[tuple[str, str]]:
        moves = [(old_path, new_path)]
        if self._repair_normalize_path(old_path) == self._repair_normalize_path(new_path):
            return moves
        old_stem = os.path.splitext(old_path)[0]
        new_stem = os.path.splitext(new_path)[0]
        seen_sources = {self._repair_normalize_path(old_path)}
        candidates = [thumbnail_path] if thumbnail_path else []
        candidates.extend(f"{old_stem}{extension}" for extension in (".jpg", ".jpeg", ".png", ".webp", ".nfo"))
        for source in candidates:
            source_key = self._repair_normalize_path(source)
            if not source_key or source_key in seen_sources or not os.path.isfile(source):
                continue
            seen_sources.add(source_key)
            moves.append((source, f"{new_stem}{os.path.splitext(source)[1] or '.jpg'}"))
        return moves

    def _rename_repair_file(
        self,
        old_path: str,
        new_path: str,
        thumbnail_path: str = "",
    ) -> tuple[bool, str, str]:
        if self._repair_normalize_path(old_path) == self._repair_normalize_path(new_path):
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
        if os.path.lexists(new_path):
            return False, "", tr(
                f"Target file already exists: {new_path}",
                f"目标文件已存在：{new_path}",
                f"変更先ファイルは既に存在します: {new_path}",
            )

        old_stem = os.path.splitext(old_path)[0]
        moves = self._repair_file_moves(old_path, new_path, thumbnail_path)
        sidecars = moves[1:]

        target_sources: dict[str, str] = {}
        for source, target in moves:
            source_key = self._repair_normalize_path(source)
            target_key = self._repair_normalize_path(target)
            if target_key in target_sources and target_sources[target_key] != source_key:
                return False, "", tr(
                    f"Several files would use the same target: {target}",
                    f"多个文件将使用同一目标：{target}",
                    f"複数のファイルが同じ変更先を使用します: {target}",
                )
            target_sources[target_key] = source_key
            if source_key != target_key and os.path.lexists(target):
                return False, "", tr(
                    f"Sidecar target already exists: {target}",
                    f"附属文件目标已存在：{target}",
                    f"関連ファイルの変更先が既に存在します: {target}",
                )

        renamed: list[tuple[str, str]] = []
        try:
            for source, target in moves:
                if self._repair_normalize_path(source) == self._repair_normalize_path(target):
                    continue
                os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
                # Recheck immediately before each move; the batch plan may be
                # stale if another program created a target in the meantime.
                if os.path.lexists(target):
                    raise FileExistsError(target)
                self._move_repair_file(source, target)
                renamed.append((source, target))
        except Exception as exc:
            for source, target in reversed(renamed):
                try:
                    if os.path.exists(target) and not os.path.exists(source):
                        os.makedirs(os.path.dirname(os.path.abspath(source)), exist_ok=True)
                        self._move_repair_file(target, source)
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

    @staticmethod
    def _move_repair_file(source: str, target: str):
        """Move a repair file without shutil.move's overwrite-on-error fallback."""

        try:
            if os.name == "nt":
                # Windows rename rejects an existing destination atomically.
                os.rename(source, target)
            else:
                # A hard link provides the same no-replace guarantee on Unix.
                os.link(source, target)
                try:
                    os.unlink(source)
                except OSError:
                    os.unlink(target)
                    raise
            return
        except OSError as exc:
            if exc.errno != errno.EXDEV and getattr(exc, "winerror", None) != 17:
                raise

        # Organizing can cross drives. Exclusively creating the destination
        # also rejects files created by another process after the preflight.
        created = False
        try:
            with open(source, "rb") as source_stream, open(target, "xb") as target_stream:
                created = True
                shutil.copyfileobj(source_stream, target_stream, length=1024 * 1024)
            shutil.copystat(source, target)
            os.unlink(source)
        except Exception:
            if created:
                os.unlink(target)
            raise

