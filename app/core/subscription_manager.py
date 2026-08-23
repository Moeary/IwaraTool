"""Subscription source, refresh, and metadata operations."""
from __future__ import annotations

import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

from ..config import app_config
from ..i18n import tr
from ..signal_bus import signal_bus
from .api import IwaraAPI
from .models import DownloadTask
from .task_metadata import (
    SUBSCRIPTION_UNAVAILABLE_STATE as _SUBSCRIPTION_UNAVAILABLE_STATE,
    _compact_video_raw_json,
    _dict_or_empty,
    _iwara_image_url,
    _subscription_download_block_from_error,
    _subscription_download_block_from_video_info,
    _subscription_item_from_video,
    _subscription_thumbnail_url,
)


class SubscriptionManagerMixin:
    def add_following_subscription(self) -> int:
        return self.subscriptions.add_source(
            "feed",
            "subscribed",
            tr("Following Feed", "账号订阅流", "購読フィード"),
            source_origin="account",
        )

    def import_followed_author_subscriptions(self) -> dict[str, Any]:
        """Import followed authors from the logged-in account.

        Prefer the explicit following-user endpoint. If it cannot be used
        (usually because the saved credential is an email rather than username),
        fall back to deriving authors from the subscribed video feed.
        """
        if not self.api.token:
            return {
                "imported": 0,
                "method": "",
                "error": tr("Please login first", "请先登录账号", "先にログインしてください"),
            }

        username = (app_config.username or "").strip()
        if username and "@" not in username:
            user_id, err = self._api_call("get_user_id", username)
            if user_id:
                users, follow_err = self._api_call("get_user_following", user_id)
                if not follow_err:
                    imported = self._add_author_sources_from_users(users)
                    return {
                        "imported": imported,
                        "method": "following",
                        "error": "",
                    }
                err = follow_err
            signal_bus.log_message.emit(
                tr(
                    f"[Subscriptions] following endpoint failed, fallback to feed authors: {err}",
                    f"[订阅] 关注作者接口失败，回退为从订阅视频流提取作者: {err}",
                    f"[購読] フォロー取得失敗、購読フィードから作者を抽出: {err}",
                )
            )

        cap = max(500, self._subscription_fetch_limit() or 500)
        videos, err = self._api_call("get_subscribed_videos", max_results=cap)
        if err:
            return {"imported": 0, "method": "feed", "error": err}
        imported = self._add_author_sources_from_videos(videos)
        return {
            "imported": imported,
            "method": "feed",
            "error": "" if imported else tr(
                "No authors found in subscribed feed",
                "订阅视频流里没有提取到作者",
                "購読フィードから作者を抽出できませんでした",
            ),
        }

    def add_author_subscription(
        self,
        username: str,
        *,
        title: str = "",
        remote_id: str = "",
        avatar_url: str = "",
        source_url: str = "",
        source_origin: str = "",
    ) -> int:
        """Add or re-enable one local author subscription source."""

        username = str(username or "").strip().lstrip("@").strip("/")
        if not username:
            return 0
        kwargs: dict[str, Any] = {
            "avatar_url": str(avatar_url or "").strip(),
        }
        # Keep the call shape used by older manager fakes stable while making
        # the durable Oreno author URL opt-in for bridge results.
        if str(source_url or "").strip():
            kwargs["source_url"] = str(source_url).strip()
        if str(source_origin or "").strip():
            kwargs["source_origin"] = str(source_origin).strip()
        return self.subscriptions.add_source(
            "author",
            username,
            str(title or username).strip() or username,
            str(remote_id or "").strip(),
            **kwargs,
        )

    def add_playlist_subscription(self, playlist_id: str) -> int:
        playlist_id = playlist_id.strip().strip("/")
        return self.subscriptions.add_source(
            "playlist",
            playlist_id,
            tr(f"Playlist {playlist_id}", f"播放列表 {playlist_id}", f"プレイリスト {playlist_id}"),
            source_origin="playlist",
        )

    def detect_subscription_source(self, raw: str) -> tuple[str, str] | None:
        parsed = self._parse_iwara_url(raw)
        if not parsed:
            return None
        kind, value = parsed
        if kind == "user":
            return "author", str(value)
        if kind == "playlist":
            return "playlist", str(value)
        return None

    def add_detected_subscription_source(self, kind: str, key: str) -> int:
        if kind == "playlist":
            return self.add_playlist_subscription(key)
        if kind == "author":
            return self.add_author_subscription(key)
        return 0

    def get_subscription_sources(self) -> list[dict[str, Any]]:
        sources = self.subscriptions.list_sources()
        if not sources:
            return sources
        counts_by_source: dict[int, dict[str, int]] = {}
        items = self.subscriptions.list_items(None)
        video_ids = [str(item.get("video_id", "") or "") for item in items]
        history_records = self.history.get_records(video_ids)
        for item in items:
            source_id = int(item.get("source_id", 0) or 0)
            video_id = str(item.get("video_id", "") or "")
            if not source_id or not video_id:
                continue
            counts = counts_by_source.setdefault(
                source_id,
                {"downloaded_count": 0, "undownloaded_count": 0, "unavailable_count": 0},
            )
            if video_id in history_records:
                counts["downloaded_count"] += 1
            elif str(item.get("download_state", "") or "") == _SUBSCRIPTION_UNAVAILABLE_STATE:
                counts["unavailable_count"] += 1
            else:
                counts["undownloaded_count"] += 1
        for source in sources:
            source_id = int(source.get("id", 0) or 0)
            counts = counts_by_source.get(source_id, {})
            source["downloaded_count"] = int(counts.get("downloaded_count", 0) or 0)
            source["undownloaded_count"] = int(counts.get("undownloaded_count", 0) or 0)
            source["unavailable_count"] = int(counts.get("unavailable_count", 0) or 0)
        return sources

    def get_subscription_storage_info(self) -> dict[str, str]:
        return {
            "db_path": self.subscriptions.db_path,
            "backup_path": self.subscriptions.backup_path,
        }

    def get_subscription_items(self, source_id: int | None = None) -> list[dict[str, Any]]:
        items = self.subscriptions.list_items(source_id)
        video_ids = [str(item.get("video_id", "") or "") for item in items]
        history_records = self.history.get_records(video_ids)
        with self._lock:
            task_info_by_video_id = {
                task.video_id.lower(): (task.status.value, task.error_msg)
                for task in self._tasks.values()
                if task.video_id
            }
        for item in items:
            video_id = str(item.get("video_id", "") or "")
            history_record = history_records.get(video_id)
            file_path = str(history_record.get("file_path", "") or "") if history_record else ""
            history_thumbnail_path = (
                str(history_record.get("thumbnail_path", "") or "") if history_record else ""
            )
            thumbnail_url = str(item.get("thumbnail_url", "") or "")
            cached_thumbnail_path = self._ensure_subscription_thumbnail_cache(
                video_id,
                thumbnail_url,
                history_thumbnail_path,
            )
            task_status, task_error = task_info_by_video_id.get(video_id.lower(), ("", ""))
            download_state = str(item.get("download_state", "") or "")
            download_reason = str(item.get("download_reason", "") or "")
            if not download_state and task_error:
                inferred_state, inferred_reason = _subscription_download_block_from_error(task_error)
                if inferred_state:
                    download_state = inferred_state
                    download_reason = inferred_reason or task_error
                    self.subscriptions.update_item_download_state(video_id, download_state, download_reason)
            item["downloaded"] = bool(history_record)
            item["download_file_path"] = file_path
            item["download_file_exists"] = bool(file_path and os.path.exists(file_path))
            item["thumbnail_path"] = cached_thumbnail_path or (
                history_thumbnail_path
                if history_thumbnail_path and os.path.isfile(history_thumbnail_path)
                else ""
            )
            item["task_status"] = task_status
            item["queued"] = bool(task_status)
            item["download_state"] = download_state
            item["download_reason"] = download_reason
            item["downloadable"] = not bool(history_record) and not bool(task_status) and not download_state
        return items

    def remove_subscription_source(self, source_id: int):
        self.remove_subscription_sources([source_id])

    def remove_subscription_sources(self, source_ids: list[int]) -> int:
        return self.subscriptions.remove_sources(source_ids)

    def cache_subscription_thumbnail(
        self,
        video_id: str,
        thumbnail_url: str,
        *,
        force: bool = False,
        api_client: IwaraAPI | None = None,
    ) -> str:
        """Cache a subscription cover and return its local path on success."""
        video_id = str(video_id or "").strip()
        thumbnail_url = str(thumbnail_url or "").strip()
        if not video_id:
            return ""

        # Author/feed list endpoints frequently return only a video stub.  In
        # that case there is no file ID yet, so derive the Iwara image URL from
        # the canonical video detail API instead of silently skipping the
        # cover.  Persist the resolved URL so subsequent refreshes do not need
        # another metadata request.
        if not thumbnail_url:
            if api_client is None:
                video_info, _error = self._api_call("get_video_info", video_id)
            else:
                video_info, _error = api_client.get_video_info(video_id)
            if isinstance(video_info, dict):
                thumbnail_url = _subscription_thumbnail_url(video_info)
                if thumbnail_url:
                    self.subscriptions.update_item_thumbnail_url(video_id, thumbnail_url)

        path = self._subscription_thumbnail_cache_path(video_id, thumbnail_url)
        if not path:
            return ""
        if not force and os.path.isfile(path) and os.path.getsize(path) > 0:
            return path
        history = self.history.get_record(str(video_id or ""))
        history_path = str(history.get("thumbnail_path", "") or "") if history else ""
        if not force:
            reused = self._ensure_subscription_thumbnail_cache(video_id, thumbnail_url, history_path)
            if reused:
                return reused
        if api_client is None:
            client = self.api
            token = self._current_token()
        else:
            client = api_client
            token = client.token or ""
        headers = {
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            "Referer": "https://www.iwara.tv/",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        fetched = self.subscription_image_cache.get_or_fetch(
            "video",
            str(video_id or ""),
            thumbnail_url,
            session=client.scraper,
            headers=headers,
            force=force,
        )
        if fetched:
            return fetched
        return ""

    def set_subscription_enabled(self, source_id: int, enabled: bool):
        self.subscriptions.set_source_enabled(source_id, enabled)

    def refresh_subscription_source_avatar(self, source_id: int) -> dict[str, Any]:
        source = self.subscriptions.get_source(source_id)
        if not source:
            return {
                "source_id": int(source_id),
                "avatar_url": "",
                "avatar_path": "",
                "error": tr("Subscription source does not exist", "订阅源不存在", "購読元が存在しません"),
            }
        if str(source.get("source_type", "") or "") != "author":
            return {
                "source_id": int(source_id),
                "avatar_url": "",
                "avatar_path": "",
                "error": "",
            }

        avatar_path = str(source.get("avatar_path", "") or "")
        avatar_url = str(source.get("avatar_url", "") or "")
        if avatar_path and os.path.isfile(avatar_path) and os.path.getsize(avatar_path) > 0:
            return {
                "source_id": int(source_id),
                "avatar_url": avatar_url,
                "avatar_path": avatar_path,
                "error": "",
            }

        if not avatar_url:
            username = str(source.get("source_key", "") or "").strip()
            profile, err = self._api_call("get_user_profile", username)
            if not profile:
                return {
                    "source_id": int(source_id),
                    "avatar_url": "",
                    "avatar_path": "",
                    "error": err,
                }
            user = _dict_or_empty(profile.get("user"))
            avatar = _dict_or_empty(user.get("avatar"))
            avatar_url = _iwara_image_url(avatar, variant="thumbnail")
            remote_id = str(user.get("id", "") or "").strip()
            if remote_id and not str(source.get("remote_id", "") or ""):
                self.subscriptions.update_source_remote_id(source_id, remote_id)
            if not avatar_url:
                self.subscriptions.update_source_avatar(source_id, "", "")
                return {
                    "source_id": int(source_id),
                    "avatar_url": "",
                    "avatar_path": "",
                    "error": "",
                }
            avatar_path = self._subscription_avatar_cache_path(source, avatar_url)
        else:
            avatar_path = avatar_path or self._subscription_avatar_cache_path(source, avatar_url)

        if self._download_subscription_avatar(avatar_url, avatar_path):
            self.subscriptions.update_source_avatar(source_id, avatar_url, avatar_path)
            return {
                "source_id": int(source_id),
                "avatar_url": avatar_url,
                "avatar_path": avatar_path,
                "error": "",
            }

        return {
            "source_id": int(source_id),
            "avatar_url": avatar_url,
            "avatar_path": "",
            "error": tr("Avatar download failed", "头像下载失败", "アバター保存に失敗しました"),
        }

    def mark_subscription_items_seen(self, video_ids: list[str]):
        self.subscriptions.mark_items_seen(video_ids)

    def mark_subscription_items_downloaded(self, video_ids: list[str]) -> int:
        ids = [str(v or "").strip() for v in video_ids if str(v or "").strip()]
        if not ids:
            return 0
        items = self.subscriptions.get_items_by_video_ids(ids)
        known = {str(item.get("video_id", "") or "") for item in items}
        for video_id in ids:
            if video_id not in known:
                items.append({"video_id": video_id, "source_url": f"https://www.iwara.tv/video/{video_id}"})
        marked = self.mark_video_items_downloaded(items)
        if marked:
            self.subscriptions.mark_items_seen(ids)
        return marked

    def restore_subscription_items_downloaded(self, video_ids: list[str]) -> int:
        ids = list(dict.fromkeys(str(v or "").strip() for v in video_ids if str(v or "").strip()))
        restored = 0
        for video_id in ids:
            record = self.history.get_record(video_id)
            if not record:
                continue
            file_path = str(record.get("file_path", "") or "")
            if file_path and os.path.exists(file_path):
                continue
            self.history.remove(video_id)
            restored += 1
        if restored:
            signal_bus.log_message.emit(
                tr(
                    f"[History] restored {restored} moved subscription videos",
                    f"[历史] 已还原 {restored} 个订阅视频的已移走状态",
                    f"[履歴] 移動済み状態を {restored} 件解除しました",
                )
            )
        return restored

    def mark_video_items_downloaded(self, items: list[dict[str, Any]]) -> int:
        marked = 0
        seen: set[str] = set()
        for item in items:
            video_id = str(item.get("video_id", "") or item.get("id", "") or "").strip()
            if not video_id or video_id.lower() in seen:
                continue
            seen.add(video_id.lower())
            existing = self.history.get_record(video_id, include_raw=True)
            meta = self._history_meta_from_item(item, existing)
            self.history.upsert_downloaded(meta)
            marked += 1
        if marked:
            signal_bus.log_message.emit(
                tr(
                    f"[History] marked {marked} videos as downloaded/moved",
                    f"[历史] 已标记 {marked} 个视频为已下载/已移走",
                    f"[履歴] {marked} 件を保存済み/移動済みとしてマーク",
                )
            )
        return marked

    def mark_subscription_source_seen(self, source_id: int):
        self.subscriptions.mark_source_seen(source_id)

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
            source_url = str(fallback_by_id.get(video_id, {}).get("source_url", "") or f"https://www.iwara.tv/video/{video_id}")
            video_info, err = self._api_call("get_video_info", video_id)
            if not video_info:
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
                    continue
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
                continue

            unavailable_reason = self._video_unavailable_reason(video_info)
            if unavailable_reason:
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
                    continue
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
                continue

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

    def refresh_all_subscriptions(
        self,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Serialize manual and timed refreshes to avoid duplicate API work."""
        if not self._subscription_refresh_guard.acquire(blocking=False):
            return {
                **self._subscription_refresh_summary([]),
                "busy": True,
                "errors": [
                    {
                        "error": tr(
                            "A subscription refresh is already running",
                            "已有订阅刷新正在运行",
                            "購読更新は既に実行中です",
                        )
                    }
                ],
            }
        try:
            return self._refresh_all_subscriptions_impl(progress_callback)
        finally:
            self._subscription_refresh_guard.release()

    def _refresh_all_subscriptions_impl(
        self,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Refresh enabled sources and optionally report source-level progress."""
        sources = [
            source
            for source in self.get_subscription_sources()
            if int(source.get("enabled", 1) or 0)
        ]
        total = len(sources)
        if not sources:
            return self._subscription_refresh_summary([])

        # Keep monkeypatched/fake API dispatchers deterministic for tests and
        # integrations.  The production bound method uses isolated sessions
        # below, so one slow source no longer blocks every other source.
        if not self._uses_default_api_dispatch():
            summaries: list[dict[str, Any]] = []
            for index, source in enumerate(sources, start=1):
                source_id = int(source["id"])
                source_title = str(source.get("title", "") or source.get("source_key", "") or "")
                if progress_callback:
                    progress_callback(
                        {
                            "stage": "started",
                            "index": index,
                            "total": total,
                            "source_id": source_id,
                            "title": source_title,
                        }
                    )
                summary = self.refresh_subscription_source(source_id)
                summaries.append(summary)
                if progress_callback:
                    progress_callback(
                        {
                            "stage": "finished",
                            "index": index,
                            "total": total,
                            "source_id": source_id,
                            "title": str(summary.get("title", "") or source_title),
                            "summary": summary,
                        }
                    )
            return self._subscription_refresh_summary(summaries)

        worker_count = min(self._subscription_refresh_workers(), total)
        source_by_id = {int(source["id"]): source for source in sources}
        index_by_id = {int(source["id"]): index for index, source in enumerate(sources, start=1)}

        for index, source in enumerate(sources, start=1):
            if progress_callback:
                progress_callback(
                    {
                        "stage": "started",
                        "index": index,
                        "total": total,
                        "source_id": int(source["id"]),
                        "title": str(source.get("title", "") or source.get("source_key", "") or ""),
                    }
                )

        summaries_by_id: dict[int, dict[str, Any]] = {}

        def refresh_one(source_id: int) -> dict[str, Any]:
            client = self.create_worker_api_client()
            try:
                return self.refresh_subscription_source(source_id, api_client=client)
            finally:
                self.close_worker_api_client(client)

        with ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix="subscription-refresh",
        ) as executor:
            futures = {
                executor.submit(refresh_one, int(source["id"])): int(source["id"])
                for source in sources
            }
            for future in as_completed(futures):
                source_id = futures[future]
                try:
                    summary = future.result()
                except Exception as exc:
                    source = source_by_id[source_id]
                    summary = {
                        "source_id": source_id,
                        "title": str(source.get("title", "") or source.get("source_key", "") or ""),
                        "new": 0,
                        "total": self.subscriptions.count_items(source_id),
                        "downloaded": 0,
                        "fetched": 0,
                        "error": str(exc),
                    }
                summaries_by_id[source_id] = summary
                if progress_callback:
                    source = source_by_id[source_id]
                    progress_callback(
                        {
                            "stage": "finished",
                            "index": index_by_id[source_id],
                            "total": total,
                            "source_id": source_id,
                            "title": str(
                                summary.get("title", "")
                                or source.get("title", "")
                                or source.get("source_key", "")
                            ),
                            "summary": summary,
                        }
                    )

        summaries = [summaries_by_id[int(source["id"])] for source in sources]
        return self._subscription_refresh_summary(summaries)

    def refresh_subscription_source(
        self,
        source_id: int,
        *,
        ignore_enabled: bool = False,
        api_client: IwaraAPI | None = None,
    ) -> dict[str, Any]:
        def api_call(method_name: str, *args, **kwargs):
            if api_client is None:
                return self._api_call(method_name, *args, **kwargs)
            return getattr(api_client, method_name)(*args, **kwargs)

        source = self.subscriptions.get_source(source_id)
        if not source:
            return {
                "source_id": source_id,
                "title": "",
                "new": 0,
                "total": 0,
                "downloaded": 0,
                "fetched": 0,
                "error": tr("Subscription source does not exist", "订阅源不存在", "購読元が存在しません"),
            }
        if not ignore_enabled and not int(source.get("enabled", 1) or 0):
            return {
                "source_id": source_id,
                "title": str(source.get("title", "") or ""),
                "new": 0,
                "total": self.subscriptions.count_items(source_id),
                "downloaded": 0,
                "fetched": 0,
                "error": tr("Subscription source is disabled", "订阅源已停用", "購読元は無効です"),
            }

        source_type = str(source.get("source_type", "") or "")
        source_key = str(source.get("source_key", "") or "")
        title = str(source.get("title", "") or source_key)
        videos: list[dict] = []
        err = ""
        cap = self._subscription_fetch_limit()
        known_video_ids = self._known_subscription_video_ids(source_id)
        source_origin = str(source.get("source_origin", "") or "").strip().casefold()
        incremental = self._subscription_incremental_refresh() and source_origin == "account"
        stop_after_video_ids = known_video_ids if incremental else None

        if source_type == "feed":
            try:
                videos, err = api_call(
                    "get_subscribed_videos",
                    max_results=cap,
                    known_video_ids=stop_after_video_ids,
                )
            except TypeError as exc:
                if "known_video_ids" not in str(exc):
                    raise
                videos, err = api_call("get_subscribed_videos", max_results=cap)
        elif source_type == "author":
            remote_id = str(source.get("remote_id", "") or "")
            avatar_url = str(source.get("avatar_url", "") or "")
            # Refresh also repairs older/manual author sources that lack a
            # display name, remote ID, or avatar URL.
            needs_profile = not remote_id or not avatar_url or title == source_key
            if needs_profile:
                try:
                    profile, _profile_err = api_call("get_user_profile", source_key)
                except Exception:
                    profile = None
                if profile:
                    user = _dict_or_empty(profile.get("user"))
                    profile_title = str(user.get("name", "") or source_key).strip() or source_key
                    remote_id = str(user.get("id", "") or remote_id).strip()
                    avatar_url = _iwara_image_url(_dict_or_empty(user.get("avatar")), variant="thumbnail") or avatar_url
                    self.subscriptions.update_source_profile(
                        source_id,
                        title=profile_title,
                        remote_id=remote_id,
                        avatar_url=avatar_url,
                    )
                    title = profile_title
            if not remote_id and not err:
                remote_id, err = api_call("get_user_id", source_key)
                if remote_id:
                    self.subscriptions.update_source_remote_id(source_id, remote_id)
            if remote_id:
                try:
                    videos = api_call(
                        "get_user_videos",
                        remote_id,
                        known_video_ids=stop_after_video_ids,
                    )
                except TypeError as exc:
                    if "known_video_ids" not in str(exc):
                        raise
                    videos = api_call("get_user_videos", remote_id)
        elif source_type == "playlist":
            videos = api_call("get_playlist_videos", source_key)
        else:
            err = tr(
                f"Unknown subscription type: {source_type}",
                f"未知订阅类型: {source_type}",
                f"不明な購読タイプ: {source_type}",
            )

        if err:
            self.subscriptions.touch_source_checked(source_id)
            return {
                "source_id": source_id,
                "title": title,
                "new": 0,
                "total": self.subscriptions.count_items(source_id),
                "downloaded": 0,
                "fetched": len(videos),
                "error": err,
            }

        normalized_items = [_subscription_item_from_video(video) for video in videos]
        known_casefold = {video_id.casefold() for video_id in known_video_ids}
        new_video_ids = list(
            dict.fromkeys(
                str(item.get("video_id", "") or "").strip()
                for item in normalized_items
                if str(item.get("video_id", "") or "").strip()
                and str(item.get("video_id", "") or "").strip().casefold() not in known_casefold
            )
        )
        if incremental:
            validation_items = [
                item
                for item in normalized_items
                if str(item.get("video_id", "") or "").strip().casefold() not in known_casefold
            ]
        else:
            validation_items = normalized_items
        new_count, total_count = self.subscriptions.upsert_items(source_id, normalized_items)
        unavailable_checked = self._validate_subscription_unavailable_items(
            validation_items,
            api_client=api_client,
        )
        self.subscriptions.touch_source_checked(source_id)
        items = self.get_subscription_items(source_id)
        downloaded_count = sum(1 for item in items if item.get("downloaded"))
        unavailable_count = sum(1 for item in items if str(item.get("download_state", "") or "") == _SUBSCRIPTION_UNAVAILABLE_STATE)
        return {
            "source_id": source_id,
            "title": title,
            "new": new_count,
            "total": total_count,
            "downloaded": downloaded_count,
            "unavailable": unavailable_count,
            "unavailable_checked": unavailable_checked,
            "fetched": len(videos),
            "new_video_ids": new_video_ids,
            "error": "",
        }

    def _validate_subscription_unavailable_items(
        self,
        items: list[dict[str, Any]],
        *,
        api_client: IwaraAPI | None = None,
    ) -> int:
        ids = list(
            dict.fromkeys(
                str(item.get("video_id", "") or "").strip()
                for item in items
                if str(item.get("video_id", "") or "").strip()
            )
        )
        if not ids:
            return 0
        history_records = self.history.get_records(ids)
        with self._lock:
            active_video_ids = {task.video_id.lower() for task in self._tasks.values() if task.video_id}

        candidates: list[str] = []
        for item in self.subscriptions.get_items_by_video_ids(ids):
            video_id = str(item.get("video_id", "") or "").strip()
            if not video_id or video_id in history_records or video_id.lower() in active_video_ids:
                continue
            download_state = str(item.get("download_state", "") or "")
            checked_at = str(item.get("download_checked_at", "") or "")
            if download_state == _SUBSCRIPTION_UNAVAILABLE_STATE or not checked_at:
                candidates.append(video_id)

        checked = 0
        api_call = (
            self._api_call
            if api_client is None
            else lambda method_name, *args, **kwargs: getattr(api_client, method_name)(*args, **kwargs)
        )
        for video_id in candidates:
            video_info, err = api_call("get_video_info", video_id)
            if not video_info:
                download_state, download_reason = _subscription_download_block_from_error(err)
                if download_state:
                    self.subscriptions.update_item_download_state(video_id, download_state, download_reason or err)
                    checked += 1
                continue

            download_state, download_reason = _subscription_download_block_from_video_info(video_info)
            if download_state:
                self.subscriptions.update_item_download_state(video_id, download_state, download_reason)
                checked += 1
                continue
            if str(video_info.get("fileUrl", "") or ""):
                self.subscriptions.update_item_download_state(video_id, "", "")
                checked += 1
        return checked

    def _known_subscription_video_ids(self, source_id: int) -> set[str]:
        return {
            str(item.get("video_id", "") or "").strip()
            for item in self.subscriptions.list_items(source_id)
            if str(item.get("video_id", "") or "").strip()
        }

    @staticmethod
    def _subscription_refresh_workers() -> int:
        try:
            value = int(app_config.get_ui_value("subscription_refresh_workers_v1", 3) or 3)
        except (TypeError, ValueError):
            value = 3
        return max(1, min(8, value))

    @staticmethod
    def _subscription_incremental_refresh() -> bool:
        value = app_config.get_ui_value("subscription_incremental_refresh_v1", True)
        if isinstance(value, str):
            return value.strip().casefold() not in {"0", "false", "off", "no"}
        return bool(value)

    def _uses_default_api_dispatch(self) -> bool:
        # Runtime import avoids a module cycle while still distinguishing the
        # production bound method from lightweight test fakes.
        from .manager import DownloadManager

        method = getattr(self, "_api_call", None)
        return (
            getattr(method, "__func__", None) is DownloadManager._api_call
            and type(self.api) is IwaraAPI
        )

    @staticmethod
    def _subscription_refresh_summary(summaries: list[dict[str, Any]]) -> dict[str, Any]:
        new_video_ids = list(
            dict.fromkeys(
                str(video_id or "").strip()
                for summary in summaries
                for video_id in list(summary.get("new_video_ids", []) or [])
                if str(video_id or "").strip()
            )
        )
        return {
            "sources": len(summaries),
            "new": sum(int(s.get("new", 0) or 0) for s in summaries),
            "total": sum(int(s.get("total", 0) or 0) for s in summaries),
            "downloaded": sum(int(s.get("downloaded", 0) or 0) for s in summaries),
            "unavailable": sum(int(s.get("unavailable", 0) or 0) for s in summaries),
            "unavailable_checked": sum(int(s.get("unavailable_checked", 0) or 0) for s in summaries),
            "errors": [s for s in summaries if s.get("error")],
            "new_video_ids": new_video_ids,
            "details": summaries,
        }

    @staticmethod
    def _subscription_fetch_limit() -> int:
        if not app_config.search_limit_enabled:
            return 0
        return max(1, int(app_config.search_limit_count or 100))

    def _add_author_sources_from_users(self, users: list[dict]) -> int:
        imported = 0
        for entry in users:
            user = entry.get("user") if isinstance(entry, dict) else None
            if not isinstance(user, dict):
                user = entry if isinstance(entry, dict) else {}
            username = str(user.get("username") or "").strip()
            if not username:
                continue
            title = str(user.get("name") or username).strip()
            remote_id = str(user.get("id") or "").strip()
            avatar_url = _iwara_image_url(_dict_or_empty(user.get("avatar")), variant="thumbnail")
            if self.subscriptions.add_source(
                "author",
                username,
                title,
                remote_id,
                avatar_url=avatar_url,
                source_origin="account",
            ):
                imported += 1
        return imported

    def _add_author_sources_from_videos(self, videos: list[dict]) -> int:
        users_by_name: dict[str, dict] = {}
        for video in videos:
            user = video.get("user")
            if not isinstance(user, dict):
                continue
            username = str(user.get("username") or "").strip()
            if username:
                users_by_name[username.lower()] = user
        return self._add_author_sources_from_users(list(users_by_name.values()))

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

        author = str(value("author", default="") or "")
        if not author:
            author = str(user.get("username") or user.get("name") or "")

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
        author = str(user.get("username", "") or user.get("name", "") or "")
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

