"""Create tasks for videos, a user's uploads, a playlist or a search query."""

from __future__ import annotations

import os
import json
import uuid
from typing import Any

from ...config import app_config
from ...i18n import tr
from ...signal_bus import signal_bus
from ..models import DownloadTask, TaskStatus


class EnqueueingMixin:
    """Create tasks for videos, a user's uploads, a playlist or a search query."""

    def enqueue_video_ids(
        self,
        video_ids: list[str],
        *,
        source_label: str = "",
        priority: int = 0,
        rule_id: str = "",
    ) -> int:
        """Queue a list of video ids and return how many were accepted for parsing."""
        items = [
            (str(raw_id or "").strip(), f"https://www.iwara.tv/video/{str(raw_id or '').strip()}")
            for raw_id in video_ids
            if str(raw_id or "").strip()
        ]
        summary = self._enqueue_video_ids_bulk(
            items,
            source_label=source_label,
            priority=priority,
            rule_id=rule_id,
        )
        return int(summary.get("queued", 0) or 0)

    def _enqueue_video_id(self, video_id: str, original_url: str, *, rule_id: str = ""):
        self._enqueue_video_ids_bulk([(video_id, original_url)], rule_id=rule_id)

    def _enqueue_video_ids_bulk(
        self,
        items: list[tuple[str, str]],
        *,
        source_label: str = "",
        priority: int = 0,
        rule_id: str = "",
    ) -> dict[str, int]:
        rule_id = self._normalize_rule_id(rule_id)
        rule_payload_json = json.dumps(
            self._rule_payload_for_id(rule_id),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        normalized, skipped_empty, skipped_input_duplicate = self._normalize_enqueue_items(items)
        existing_paths = self._existing_paths_for(normalized)
        skipped_existing = len(existing_paths)
        limit = app_config.max_concurrent
        added_infos, skipped_duplicate, queued_count, active_count, promoted = self._add_queued_tasks(
            normalized, existing_paths, priority=priority, rule_id=rule_id, rule_payload_json=rule_payload_json,
        )
        for task_id in promoted:  # waiting for confirmation until now: same row, new state
            signal_bus.task_status_changed.emit(task_id, TaskStatus.QUEUED_META.value)
        if promoted:
            signal_bus.staging_changed.emit()

        if added_infos:
            signal_bus.tasks_added.emit(added_infos)
            if len(added_infos) == 1:
                info = dict(added_infos[0])
                task_id = str(info.pop("task_id"))
                signal_bus.task_added.emit(task_id, info)

        label = f" from {source_label}" if source_label else ""
        signal_bus.log_message.emit(
            tr(
                f"[Queue] added {len(added_infos)}{label}; duplicate {skipped_duplicate + skipped_input_duplicate}; existing {skipped_existing}; queued {queued_count}; active {active_count}/{limit}",
                f"[入队] 新增 {len(added_infos)} 个{('，来源: ' + source_label) if source_label else ''}；重复 {skipped_duplicate + skipped_input_duplicate}；本地已存在 {skipped_existing}；排队 {queued_count}；活动 {active_count}/{limit}",
                f"[キュー] 追加 {len(added_infos)} 件{label}; 重複 {skipped_duplicate + skipped_input_duplicate}; 既存 {skipped_existing}; 待機 {queued_count}; 稼働 {active_count}/{limit}",
            )
        )
        if added_infos or promoted:
            self._schedule_task_persist()
        self._try_activate()
        return {
            "queued": len(added_infos) + len(promoted),
            "duplicates": skipped_duplicate + skipped_input_duplicate,
            "existing": skipped_existing,
            "empty": skipped_empty,
        }

    def _add_queued_tasks(
        self,
        normalized: list[tuple[str, str]],
        existing_paths: dict[str, str],
        *,
        priority: int,
        rule_id: str,
        rule_payload_json: str,
    ) -> tuple[list[dict[str, Any]], int, int, int, list[str]]:
        """Create tasks under the lock.

        Returns ``(added infos, duplicates, queued, active, promoted ids)``; a
        video waiting for confirmation (STAGED) is promoted in place.
        """

        added_infos: list[dict[str, Any]] = []
        promoted: list[str] = []
        skipped_duplicate = 0
        with self._lock:
            for video_id, original_url in normalized:
                key = video_id.lower()
                if key in existing_paths:
                    continue
                existing = self._tasks.get(self._task_id_by_video_id.get(key, ""))
                if existing is not None and existing.status == TaskStatus.STAGED:
                    existing.status = TaskStatus.QUEUED_META
                    existing.priority = max(-100, min(100, int(priority)))
                    existing.rule_id = str(rule_id or "")
                    existing.rule_payload_json = rule_payload_json
                    self._queued_meta_ids.append(existing.task_id)
                    promoted.append(existing.task_id)
                    continue
                if key in self._task_id_by_video_id:
                    skipped_duplicate += 1
                    continue
                task_id = str(uuid.uuid4())
                task = DownloadTask(
                    task_id=task_id,
                    url=original_url,
                    video_id=video_id,
                    priority=max(-100, min(100, int(priority))),
                    rule_id=str(rule_id or ""),
                    rule_payload_json=rule_payload_json,
                )
                self._tasks[task_id] = task
                self._task_id_by_video_id[key] = task_id
                self._queued_meta_ids.append(task_id)
                added_infos.append(
                    {
                        "task_id": task_id,
                        "video_id": video_id,
                        "title": video_id,
                        "author": "",
                        "status": TaskStatus.QUEUED_META.value,
                        "priority": task.priority,
                    }
                )
            queued_count = len(self._queued_meta_ids)
            active_count = len(self._active_task_ids)
        return added_infos, skipped_duplicate, queued_count, active_count, promoted

    @staticmethod
    def _normalize_enqueue_items(items: list[tuple[str, str]]) -> tuple[list[tuple[str, str]], int, int]:
        """Drop empty and repeated ids; returns ``(items, empty count, duplicate count)``."""

        seen_input: set[str] = set()
        normalized: list[tuple[str, str]] = []
        skipped_empty = 0
        skipped_input_duplicate = 0
        for raw_video_id, original_url in items:
            video_id = str(raw_video_id or "").strip()
            if not video_id:
                skipped_empty += 1
                continue
            key = video_id.lower()
            if key in seen_input:
                skipped_input_duplicate += 1
                continue
            seen_input.add(key)
            normalized.append((video_id, original_url or f"https://www.iwara.tv/video/{video_id}"))
        return normalized, skipped_empty, skipped_input_duplicate

    def _existing_paths_for(self, normalized: list[tuple[str, str]]) -> dict[str, str]:
        """Lower-cased video id -> local file, for videos already downloaded (when skipping is on)."""

        existing_paths: dict[str, str] = {}
        if app_config.skip_existing_files and normalized:
            history_records = self.history.get_records([video_id for video_id, _ in normalized])
            file_index = self._get_existing_file_index()
            for video_id, _ in normalized:
                record = history_records.get(video_id)
                if record:
                    file_path = str(record.get("file_path", "") or "")
                    if file_path and os.path.isfile(file_path) and os.path.getsize(file_path) > 0:
                        existing_paths[video_id.lower()] = file_path
                        continue
                existing = file_index.get(video_id.lower())
                if existing and os.path.isfile(existing):
                    existing_paths[video_id.lower()] = existing
        return existing_paths

    def _enqueue_user(self, username: str, *, rule_id: str = ""):
        signal_bus.log_message.emit(
            tr(
                f"Fetching videos for user [{username}] ...",
                f"正在获取用户 [{username}] 的视频列表…",
                f"ユーザー [{username}] の動画一覧を取得中...",
            )
        )
        user_id, err = self._api_call("get_user_id", username)
        if not user_id:
            signal_bus.log_message.emit(
                tr(
                    f"[Error] Failed to get user ID: {err}",
                    f"[错误] 无法获取用户 ID: {err}",
                    f"[エラー] ユーザーIDの取得に失敗: {err}",
                )
            )
            return
        videos = self._api_call("get_user_videos", user_id)
        signal_bus.log_message.emit(
            tr(
                f"Found {len(videos)} videos for user [{username}]",
                f"用户 [{username}] 共找到 {len(videos)} 个视频",
                f"ユーザー [{username}] で {len(videos)} 件の動画を検出",
            )
        )
        self._enqueue_video_ids_bulk(
            [
                (str(video.get("id", "") or "").strip(), f"https://www.iwara.tv/video/{str(video.get('id', '') or '').strip()}")
                for video in videos
                if str(video.get("id", "") or "").strip()
            ],
            source_label=username,
            rule_id=rule_id,
        )

    def _enqueue_playlist(self, playlist_id: str, *, rule_id: str = ""):
        signal_bus.log_message.emit(
            tr(
                f"Fetching videos from playlist [{playlist_id}] ...",
                f"正在获取播放列表 [{playlist_id}] 的视频…",
                f"プレイリスト [{playlist_id}] の動画を取得中...",
            )
        )
        videos = self._api_call("get_playlist_videos", playlist_id)
        signal_bus.log_message.emit(
            tr(
                f"Found {len(videos)} videos in playlist",
                f"播放列表共找到 {len(videos)} 个视频",
                f"プレイリストで {len(videos)} 件の動画を検出",
            )
        )
        self._enqueue_video_ids_bulk(
            [
                (str(video.get("id", "") or "").strip(), f"https://www.iwara.tv/video/{str(video.get('id', '') or '').strip()}")
                for video in videos
                if str(video.get("id", "") or "").strip()
            ],
            source_label=tr("Playlist", "播放列表", "プレイリスト"),
            rule_id=rule_id,
        )

    def _enqueue_search_query(self, query_params: dict[str, str], *, rule_id: str = ""):
        if not query_params:
            signal_bus.log_message.emit(
                tr(
                    "[Error] Empty API query parameters",
                    "[错误] API 搜索参数为空",
                    "[エラー] API 検索パラメータが空です",
                )
            )
            return

        configured_cap = (
            max(0, int(app_config.search_limit_count))
            if app_config.search_limit_enabled
            else 0
        )
        query_text = "&".join(f"{k}={v}" for k, v in query_params.items())
        if configured_cap > 0:
            signal_bus.log_message.emit(
                tr(
                    f"Fetching search videos: {query_text} (cap: {configured_cap})",
                    f"正在拉取搜索结果: {query_text}（上限: {configured_cap}）",
                    f"検索結果を取得中: {query_text}（上限: {configured_cap}）",
                )
            )
        else:
            signal_bus.log_message.emit(
                tr(
                    f"Fetching search videos: {query_text}",
                    f"正在拉取搜索结果: {query_text}",
                    f"検索結果を取得中: {query_text}",
                )
            )

        videos, err = self._api_call(
            "get_videos_by_query",
            query_params,
            max_results=configured_cap,
        )
        if err:
            signal_bus.log_message.emit(
                tr(
                    f"[Warning] Search API returned partial result: {err}",
                    f"[警告] 搜索 API 返回部分结果: {err}",
                    f"[警告] 検索 API は部分結果を返しました: {err}",
                )
            )
        if not videos:
            signal_bus.log_message.emit(
                tr(
                    "No videos found for this query",
                    "该查询未找到视频",
                    "このクエリでは動画が見つかりませんでした",
                )
            )
            return

        signal_bus.log_message.emit(
            tr(
                f"Search found {len(videos)} videos",
                f"搜索共找到 {len(videos)} 个视频",
                f"検索で {len(videos)} 件の動画を検出",
            )
        )
        self._enqueue_video_ids_bulk(
            [
                (str(video.get("id", "") or "").strip(), f"https://www.iwara.tv/video/{str(video.get('id', '') or '').strip()}")
                for video in videos
                if str(video.get("id", "") or "").strip()
            ],
            source_label=tr("Search", "搜索", "検索"),
            rule_id=rule_id,
        )
