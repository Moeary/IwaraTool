"""Repair scanned files: rename / move them and add cover, NFO and history."""

from __future__ import annotations

import os
import uuid
from typing import Any, Callable

from ...i18n import pin_language, tr
from ...signal_bus import signal_bus
from ..models import DownloadTask
from ..task_metadata import _author_fields_from_user, _dict_or_empty


class RepairingMixin:
    """Repair scanned files: rename / move them and add cover, NFO and history."""

    @pin_language
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

        if not self._apply_repair_rename(task, path, target_path, cached_meta, options, result):
            return result
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
            self._repair_sidecars_and_history(task, video_id, metadata, cached_meta, source_url, options, result)
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

    def _apply_repair_rename(self, task, path: str, target_path: str, cached_meta: dict, options: dict[str, Any], result: dict[str, Any]) -> bool:
        """Rename / move the file as planned and point ``task`` at it; False if that failed."""

        if options.get("rename", True):
            thumbnail_before = str(cached_meta.get("thumbnail_path", "") or "")
            ok, new_thumbnail, rename_message = self._rename_repair_file(
                path,
                target_path,
                thumbnail_before,
            )
            if not ok:
                result.update(status="failed", message=rename_message)
                return False
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
        return True

    def _repair_sidecars_and_history(self, task, video_id: str, metadata: dict, cached_meta: dict, source_url: str, options: dict[str, Any], result: dict[str, Any]):
        """Cover, NFO and history entry for a repaired file; flags go into ``result``."""

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
