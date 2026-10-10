"""Add, detect, list, pause and remove subscription sources."""

from __future__ import annotations

from typing import Any

from ...config import app_config
from ...i18n import tr
from ...signal_bus import signal_bus
from ..task_metadata import (
    SUBSCRIPTION_UNAVAILABLE_STATE as _SUBSCRIPTION_UNAVAILABLE_STATE,
    _dict_or_empty,
    _iwara_image_url,
)


class SubscriptionSourcesMixin:
    """Add, detect, list, pause and remove subscription sources."""

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

    def remove_subscription_source(self, source_id: int):
        self.remove_subscription_sources([source_id])

    def remove_subscription_sources(self, source_ids: list[int]) -> int:
        return self.subscriptions.remove_sources(source_ids)

    def set_subscription_enabled(self, source_id: int, enabled: bool):
        self.subscriptions.set_source_enabled(source_id, enabled)

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
