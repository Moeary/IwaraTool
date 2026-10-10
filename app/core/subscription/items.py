"""Read subscription items and mark them seen / downloaded / restored."""

from __future__ import annotations

import os
from typing import Any

from ...i18n import tr
from ...signal_bus import signal_bus
from ..task_metadata import _subscription_download_block_from_error


class SubscriptionItemsMixin:
    """Read subscription items and mark them seen / downloaded / restored."""

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

    def subscription_cover_path(self, video_id: str, thumbnail_url: str = "") -> str:
        """Local path of a video's cover when it is already on disk, else ``""``.

        One file check per call: the overview resolves only the covers it is
        about to show instead of probing every video of every subscription.
        """

        video_id = str(video_id or "").strip()
        if not video_id:
            return ""
        record = self.history.get_record(video_id)
        history_path = str(record.get("thumbnail_path", "") or "") if record else ""
        cached = self._ensure_subscription_thumbnail_cache(video_id, str(thumbnail_url or ""), history_path)
        if cached:
            return cached
        return history_path if history_path and os.path.isfile(history_path) else ""

    def get_subscription_recent_items(
        self, per_source: int = 8, *, resolve_thumbnails: bool = True,
    ) -> dict[int, list[dict[str, Any]]]:
        """Latest items per source with their local download state (for the overview).

        ``resolve_thumbnails=False`` skips the per-video disk probe for cover
        files (hundreds of stat calls on a large library); callers then resolve
        the visible ones with :meth:`subscription_cover_path`.
        """

        recent = self.subscriptions.list_recent_items(per_source)
        items = [item for rows in recent.values() for item in rows]
        history_records = self.history.get_records([str(item.get("video_id", "") or "") for item in items])
        with self._lock:
            task_status_by_video_id = {
                task.video_id.lower(): task.status.value
                for task in self._tasks.values()
                if task.video_id
            }
        for item in items:
            video_id = str(item.get("video_id", "") or "")
            record = history_records.get(video_id)
            file_path = str(record.get("file_path", "") or "") if record else ""
            history_thumbnail_path = str(record.get("thumbnail_path", "") or "") if record else ""
            cached = (
                self._ensure_subscription_thumbnail_cache(
                    video_id, str(item.get("thumbnail_url", "") or ""), history_thumbnail_path,
                )
                if resolve_thumbnails
                else ""
            )
            item["downloaded"] = bool(record)
            item["download_file_exists"] = bool(file_path and os.path.exists(file_path))
            item["thumbnail_path"] = cached or (
                history_thumbnail_path
                if resolve_thumbnails and history_thumbnail_path and os.path.isfile(history_thumbnail_path)
                else ""
            )
            item["queued"] = bool(task_status_by_video_id.get(video_id.lower()))
        return recent

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
