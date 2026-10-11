"""Refresh one or all subscription sources from the API."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

from ...config import app_config
from ...i18n import tr
from ..api.client import IwaraAPI
from ..task_metadata import (
    SUBSCRIPTION_UNAVAILABLE_STATE as _SUBSCRIPTION_UNAVAILABLE_STATE,
    _dict_or_empty,
    _iwara_image_url,
    _subscription_download_block_from_error,
    _subscription_download_block_from_video_info,
    _subscription_item_from_video,
)


class SubscriptionRefreshMixin:
    """Refresh one or all subscription sources from the API."""

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
        if not sources:
            return self._subscription_refresh_summary([])

        # Keep monkeypatched/fake API dispatchers deterministic for tests and
        # integrations.  The production bound method uses isolated sessions
        # below, so one slow source no longer blocks every other source.
        if not self._uses_default_api_dispatch():
            return self._refresh_sources_sequentially(sources, progress_callback)
        return self._refresh_sources_in_parallel(sources, progress_callback)

    def _refresh_sources_sequentially(self, sources: list[dict[str, Any]], progress_callback) -> dict[str, Any]:
        total = len(sources)
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

    def _refresh_sources_in_parallel(self, sources: list[dict[str, Any]], progress_callback) -> dict[str, Any]:
        """Each source on its own API session, so one slow source does not hold up the others."""

        total = len(sources)
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
        api_call = self._source_api_call(api_client)
        source = self.subscriptions.get_source(source_id)
        skipped = self._unrefreshable_source_summary(source_id, source, ignore_enabled=ignore_enabled)
        if skipped is not None:
            return skipped

        title = str(source.get("title", "") or source.get("source_key", "") or "")
        known_video_ids = self._known_subscription_video_ids(source_id)
        source_origin = str(source.get("source_origin", "") or "").strip().casefold()
        incremental = self._subscription_incremental_refresh() and source_origin == "account"
        stop_after_video_ids = known_video_ids if incremental else None
        videos, err, title = self._fetch_source_videos(source_id, source, title, api_call, stop_after_video_ids)

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
        return self._store_refreshed_items(source_id, title, videos, known_video_ids, api_client)

    def _source_api_call(self, api_client: IwaraAPI | None):
        """Call API methods on ``api_client`` when given, else through the manager's dispatcher."""

        def api_call(method_name: str, *args, **kwargs):
            if api_client is None:
                return self._api_call(method_name, *args, **kwargs)
            return getattr(api_client, method_name)(*args, **kwargs)

        return api_call

    def _unrefreshable_source_summary(self, source_id: int, source, *, ignore_enabled: bool) -> dict[str, Any] | None:
        """The result for a missing or disabled source; None when it can be refreshed."""

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
        return None

    def _fetch_source_videos(self, source_id: int, source, title: str, api_call, stop_after_video_ids):
        """``(videos, error, title)`` for one source; the title may be repaired on the way."""

        source_type = str(source.get("source_type", "") or "")
        source_key = str(source.get("source_key", "") or "")
        cap = self._subscription_fetch_limit()
        videos: list[dict] = []
        err = ""
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
            videos, err, title = self._fetch_author_videos(source_id, source, title, api_call, stop_after_video_ids)
        elif source_type == "playlist":
            videos = api_call("get_playlist_videos", source_key)
        else:
            err = tr(
                f"Unknown subscription type: {source_type}",
                f"未知订阅类型: {source_type}",
                f"不明な購読タイプ: {source_type}",
            )
        return videos, err, title

    def _fetch_author_videos(self, source_id: int, source, title: str, api_call, stop_after_video_ids):
        source_key = str(source.get("source_key", "") or "")
        remote_id = str(source.get("remote_id", "") or "")
        avatar_url = str(source.get("avatar_url", "") or "")
        videos: list[dict] = []
        err = ""
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
        return videos, err, title

    def _store_refreshed_items(
        self, source_id: int, title: str, videos: list[dict], known_video_ids, api_client: IwaraAPI | None,
    ) -> dict[str, Any]:
        """Save fetched items, recheck unavailable ones and summarize the refresh."""

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
        new_count, total_count = self.subscriptions.upsert_items(source_id, normalized_items)

        # Incremental account refreshes intentionally stop fetching as soon as
        # they reach a known video.  That is useful for discovering new items,
        # but it also means a previously private video will not be present in
        # ``normalized_items`` after the owner grants access.  Always append
        # cached unavailable rows so their permission state can be rechecked.
        validation_items = list(normalized_items)
        validation_ids = {
            str(item.get("video_id", "") or "").strip().casefold()
            for item in validation_items
            if str(item.get("video_id", "") or "").strip()
        }
        for item in self.subscriptions.list_items(source_id):
            video_id = str(item.get("video_id", "") or "").strip()
            if (
                video_id
                and str(item.get("download_state", "") or "")
                == _SUBSCRIPTION_UNAVAILABLE_STATE
                and video_id.casefold() not in validation_ids
            ):
                validation_items.append(item)
                validation_ids.add(video_id.casefold())
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
            # A successful detail response that is no longer classified as
            # private/external proves that the old cached permission block is
            # stale.  Do not require fileUrl here: the API can return a valid
            # detail record while a file is still being prepared.
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
        from ..manager import DownloadManager

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
