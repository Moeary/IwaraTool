"""Cache subscription item thumbnails and source avatars on disk."""

from __future__ import annotations

import os
from typing import Any

from ...i18n import tr
from ..api.client import IwaraAPI
from ..task_metadata import _dict_or_empty, _iwara_image_url, _subscription_thumbnail_url


class SubscriptionMediaCacheMixin:
    """Cache subscription item thumbnails and source avatars on disk."""

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

        history = self.history.get_record(video_id)
        history_path = str(history.get("thumbnail_path", "") or "") if history else ""
        if not force:
            reused = self._ensure_subscription_thumbnail_cache(video_id, thumbnail_url, history_path)
            if reused:
                return reused
            # Only cache files are handed out, never the cover next to a download.
            reused = (self._cached_video_cover(video_id) if not thumbnail_url else "") or (
                self._seed_cover_from_download(video_id, history_path)
            )
            if reused:
                return reused

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
