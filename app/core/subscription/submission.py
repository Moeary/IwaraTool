"""Queue subscription items for download, or record them in history only."""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

from ...config import app_config
from ...i18n import tr
from ...signal_bus import signal_bus
from ..models import DownloadTask
from ..task_metadata import (
    SUBSCRIPTION_UNAVAILABLE_STATE as _SUBSCRIPTION_UNAVAILABLE_STATE,
    _author_fields_from_user,
    _compact_video_raw_json,
    _dict_or_empty,
    _subscription_download_block_from_error,
    _subscription_download_block_from_video_info,
)


class SubscriptionSubmissionMixin:
    """Queue subscription items for download, or record them in history only."""

    def enqueue_subscription_items(self, video_ids: list[str], *, rule_id: str = "") -> int:
        ids, _skipped = self._filter_downloadable_subscription_ids(video_ids)
        queued = self.enqueue_video_ids(
            ids,
            source_label=tr("Subscriptions", "订阅页", "購読"),
            rule_id=rule_id,
        )
        self.mark_subscription_items_seen(ids)
        return queued

    def submit_subscription_items(
        self,
        video_ids: list[str],
        *,
        rule_id: str = "",
    ) -> dict[str, int | str]:
        ids = list(dict.fromkeys(str(v or "").strip() for v in video_ids if str(v or "").strip()))
        ids, skipped_unavailable = self._filter_downloadable_subscription_ids(ids)
        if not ids:
            return {
                "mode": "empty",
                "queued": 0,
                "marked": 0,
                "thumbnail": 0,
                "nfo": 0,
                "failed": 0,
                "skipped_unavailable": skipped_unavailable,
            }
        rule_payload = self._rule_payload_for_id(rule_id)
        if rule_payload["download_video_file"] and not rule_payload["mark_submitted_as_downloaded"]:
            queued = self.enqueue_subscription_items(ids, rule_id=rule_id)
            return {
                "mode": "download",
                "queued": queued,
                "marked": 0,
                "thumbnail": 0,
                "nfo": 0,
                "failed": 0,
                "skipped_unavailable": skipped_unavailable,
            }
        result = self._process_subscription_items_metadata_only(ids, rule_id=rule_id)
        result["skipped_unavailable"] = skipped_unavailable
        return result

    def _filter_downloadable_subscription_ids(self, video_ids: list[str]) -> tuple[list[str], int]:
        ids = list(dict.fromkeys(str(v or "").strip() for v in video_ids if str(v or "").strip()))
        if not ids:
            return [], 0
        blocked: set[str] = set()
        for item in self.subscriptions.get_items_by_video_ids(ids):
            video_id = str(item.get("video_id", "") or "")
            if video_id and str(item.get("download_state", "") or "") == _SUBSCRIPTION_UNAVAILABLE_STATE:
                blocked.add(video_id)
        return [video_id for video_id in ids if video_id not in blocked], len(blocked)

    def _process_subscription_items_metadata_only(
        self,
        video_ids: list[str],
        *,
        rule_id: str = "",
    ) -> dict[str, int | str]:
        items = self.subscriptions.get_items_by_video_ids(video_ids)
        fallback_by_id = {
            str(item.get("video_id", "") or ""): item
            for item in items
            if str(item.get("video_id", "") or "")
        }
        result: dict[str, int | str] = {
            "mode": "metadata",
            "queued": 0,
            "marked": 0,
            "thumbnail": 0,
            "nfo": 0,
            "failed": 0,
        }
        rule_payload = self._rule_payload_for_id(rule_id)
        for video_id in video_ids:
            self._process_metadata_item(video_id, fallback_by_id, rule_id, rule_payload, result)

        if int(result["marked"]):
            self.subscriptions.mark_items_seen(video_ids)
            signal_bus.log_message.emit(
                tr(
                    f"[Subscriptions] metadata-only done: marked={result['marked']}, thumbnails={result['thumbnail']}, nfo={result['nfo']}, failed={result['failed']}",
                    f"[订阅] 仅元数据处理完成：标记={result['marked']}，封面={result['thumbnail']}，NFO={result['nfo']}，失败={result['failed']}",
                    f"[購読] メタデータのみ完了: 記録={result['marked']}、サムネイル={result['thumbnail']}、NFO={result['nfo']}、失敗={result['failed']}",
                )
            )
        return result

    def _process_metadata_item(
        self,
        video_id: str,
        fallback_by_id: dict[str, dict[str, Any]],
        rule_id: str,
        rule_payload: dict[str, Any],
        result: dict[str, int | str],
    ) -> None:
        """Save one video's cover / NFO / history entry; counts go into ``result``."""

        source_url = str(fallback_by_id.get(video_id, {}).get("source_url", "") or f"https://www.iwara.tv/video/{video_id}")
        video_info, err = self._api_call("get_video_info", video_id)
        if not video_info:
            self._record_metadata_fetch_failure(video_id, source_url, err, fallback_by_id, result)
            return

        unavailable_reason = self._video_unavailable_reason(video_info)
        if unavailable_reason:
            self._record_unavailable_metadata(video_id, source_url, video_info, unavailable_reason, fallback_by_id, result)
            return

        task = self._metadata_task_from_video_info(
            video_id,
            video_info,
            source_url,
            rule_id=rule_id,
        )
        if rule_payload["download_thumbnail"] and self._download_thumbnail(task, require_video_file=False):
            result["thumbnail"] = int(result["thumbnail"]) + 1
        if rule_payload["collect_nfo_info"] and self._write_nfo(task, require_video_file=False):
            result["nfo"] = int(result["nfo"]) + 1

        existing = self.history.get_record(task.video_id, include_raw=True)
        history_item = dict(video_info)
        history_item["video_id"] = task.video_id
        history_item["source_url"] = source_url
        meta = self._history_meta_from_item(history_item, existing)
        meta["thumbnail_path"] = task.thumbnail_path or meta.get("thumbnail_path", "")
        if rule_payload["record_to_history"]:
            self.history.upsert_downloaded(meta)
            result["marked"] = int(result["marked"]) + 1

    def _record_metadata_fetch_failure(
        self, video_id: str, source_url: str, err, fallback_by_id: dict[str, dict[str, Any]], result: dict[str, int | str],
    ) -> None:
        download_state, download_reason = _subscription_download_block_from_error(err)
        if download_state:
            self.subscriptions.update_item_download_state(video_id, download_state, download_reason or err)
            result["failed"] = int(result["failed"]) + 1
            signal_bus.log_message.emit(
                tr(
                    f"[Subscriptions] metadata unavailable for {video_id}: {download_reason or err}",
                    f"[订阅] {video_id} 元数据不可下载：{download_reason or err}",
                    f"[購読] {video_id} のメタデータ保存不可: {download_reason or err}",
                )
            )
            return
        fallback = fallback_by_id.get(video_id) or {
            "video_id": video_id,
            "source_url": source_url,
        }
        result["marked"] = int(result["marked"]) + self.mark_video_items_downloaded([fallback])
        result["failed"] = int(result["failed"]) + 1
        signal_bus.log_message.emit(
            tr(
                f"[Subscriptions] metadata fetch failed for {video_id}, marked from cached row: {err}",
                f"[订阅] {video_id} 元数据获取失败，已按缓存行标记：{err}",
                f"[購読] {video_id} のメタデータ取得失敗、キャッシュ行で記録: {err}",
            )
        )

    def _record_unavailable_metadata(
        self,
        video_id: str,
        source_url: str,
        video_info: dict[str, Any],
        unavailable_reason: str,
        fallback_by_id: dict[str, dict[str, Any]],
        result: dict[str, int | str],
    ) -> None:
        download_state, download_reason = _subscription_download_block_from_video_info(video_info)
        if download_state:
            self.subscriptions.update_item_download_state(video_id, download_state, download_reason or unavailable_reason)
            result["failed"] = int(result["failed"]) + 1
            signal_bus.log_message.emit(
                tr(
                    f"[Subscriptions] metadata unavailable for {video_id}: {download_reason or unavailable_reason}",
                    f"[订阅] {video_id} 元数据不可下载：{download_reason or unavailable_reason}",
                    f"[購読] {video_id} のメタデータ保存不可: {download_reason or unavailable_reason}",
                )
            )
            return
        fallback = fallback_by_id.get(video_id) or {
            "video_id": video_id,
            "source_url": source_url,
        }
        result["marked"] = int(result["marked"]) + self.mark_video_items_downloaded([fallback])
        result["failed"] = int(result["failed"]) + 1
        signal_bus.log_message.emit(
            tr(
                f"[Subscriptions] metadata unavailable for {video_id}: {unavailable_reason}",
                f"[订阅] {video_id} 元数据不可用：{unavailable_reason}",
                f"[購読] {video_id} のメタデータ利用不可: {unavailable_reason}",
            )
        )

    def _history_meta_from_item(
        self, item: dict[str, Any], existing: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        video_id = str(item.get("video_id", "") or item.get("id", "") or "").strip()
        user = _dict_or_empty(item.get("user"))
        file_info = _dict_or_empty(item.get("file"))

        def value(*keys: str, default: Any = "") -> Any:
            for key in keys:
                raw = item.get(key)
                if raw not in (None, ""):
                    return raw
            if existing:
                for key in keys:
                    raw = existing.get(key)
                    if raw not in (None, ""):
                        return raw
            return default

        user_author, user_display_name = _author_fields_from_user(user)
        author = str(value("author", default="") or user_author or "")
        username = str(value("username", default="") or user_display_name or author or "")

        raw_tags = item.get("tags", [])
        tags_json = json.dumps(raw_tags, ensure_ascii=False) if isinstance(raw_tags, list) else ""
        raw_json = _compact_video_raw_json(item) if "user" in item or "body" in item else ""
        if existing:
            tags_json = tags_json or str(existing.get("tags_json", "") or "")
            raw_json = raw_json or str(existing.get("raw_json", "") or "")
        return {
            "video_id": video_id,
            "title": str(value("title", default=video_id) or video_id),
            "author": author,
            "username": username,
            "published_at": str(value("published_at", "createdAt", "updatedAt", default="") or ""),
            "likes": int(value("likes", "numLikes", default=0) or 0),
            "views": int(value("views", "numViews", default=0) or 0),
            "slug": str(value("slug", default="") or ""),
            "rating": str(value("rating", default="") or ""),
            "duration": int(value("duration", default=file_info.get("duration", 0) or 0) or 0),
            "comments": int(value("comments", "numComments", default=0) or 0),
            "tags_json": tags_json,
            "raw_json": raw_json,
            "source_url": str(value("source_url", default=f"https://www.iwara.tv/video/{video_id}") or ""),
            "file_path": str(existing.get("file_path", "") or "") if existing else "",
            "thumbnail_path": str(existing.get("thumbnail_path", "") or "") if existing else "",
            "quality": str(existing.get("quality", "") or "") if existing else "",
        }

    def _metadata_task_from_video_info(
        self,
        video_id: str,
        video_info: dict[str, Any],
        source_url: str,
        *,
        rule_id: str = "",
    ) -> DownloadTask:
        user = _dict_or_empty(video_info.get("user"))
        file_info = _dict_or_empty(video_info.get("file"))
        task_video_id = str(video_info.get("id", "") or video_info.get("video_id", "") or video_id)
        title = str(video_info.get("title", "") or task_video_id)
        author, username = _author_fields_from_user(user)
        published_at = str(video_info.get("createdAt", "") or "")
        likes = int(video_info.get("numLikes", 0) or 0)
        views = int(video_info.get("numViews", 0) or 0)
        slug = str(video_info.get("slug", "") or "")
        rating = str(video_info.get("rating", "") or "")
        duration = int(file_info.get("duration", 0) or 0)
        comments = int(video_info.get("numComments", 0) or 0)
        raw_tags = video_info.get("tags", [])
        tags_json = json.dumps(raw_tags, ensure_ascii=False) if isinstance(raw_tags, list) else ""
        raw_json = _compact_video_raw_json(video_info)
        file_url = str(video_info.get("fileUrl", "") or "")
        file_id = str(file_info.get("id", "") or "")
        thumbnail_index = int(video_info.get("thumbnail", 0) or 0)
        quality = str(app_config.preferred_quality or "metadata")
        rule_payload = self._rule_payload_for_id(rule_id)

        task = DownloadTask(
            str(uuid.uuid4()),
            source_url,
            task_video_id,
            rule_id=self._normalize_rule_id(rule_id),
            rule_payload_json=json.dumps(
                rule_payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
        )
        self._apply_task_metadata(
            task,
            title=title,
            author=author,
            username=username,
            published_at=published_at,
            likes=likes,
            views=views,
            slug=slug,
            rating=rating,
            duration=duration,
            comments=comments,
            tags_json=tags_json,
            raw_json=raw_json,
            file_url=file_url,
            file_id=file_id,
            thumbnail_index=thumbnail_index,
        )
        task.quality = quality
        output_rel_path = self._build_output_relative_path(
            title=title,
            video_id=task_video_id,
            author=author,
            username=username,
            published_at=published_at,
            quality=quality,
            likes=likes,
            views=views,
            comments=comments,
            duration=duration,
            slug=slug,
            rating=rating,
            filename_template=rule_payload["filename_template"],
        )
        task.file_path = os.path.join(app_config.download_dir, output_rel_path)
        task.filename = os.path.basename(task.file_path)
        return task
