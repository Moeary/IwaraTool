"""User endpoints: ids, profiles, uploads, follows, likes and the subscription feed."""

from __future__ import annotations

from typing import Optional

from ...i18n import tr
from .endpoints import ALT_BASE_API, BASE_API
from .errors import (
    _extract_api_message,
    _friendly_http_response,
    _friendly_network_error,
    _try_response_json,
)


class UserApiMixin:
    """User endpoints: ids, profiles, uploads, follows, likes and the subscription feed."""

    def get_user_id(self, username: str) -> tuple[Optional[str], str]:
        try:
            data = self._get_json(f"{BASE_API}/profile/{username}")
            uid = data.get("user", {}).get("id")
            if uid:
                return uid, ""
            return None, tr(
                f"API did not return user.id, response: {data}",
                f"API 未返回 user.id，响应: {data}",
                f"API が user.id を返しませんでした。応答: {data}",
            )
        except Exception as exc:
            return None, str(exc)

    def get_user_profile(self, username: str) -> tuple[Optional[dict], str]:
        try:
            data = self._get_json(f"{BASE_API}/profile/{username}")
            if isinstance(data, dict) and isinstance(data.get("user"), dict):
                return data, ""
            return None, tr(
                f"API did not return profile.user, response: {data}",
                f"API 未返回 profile.user，响应: {data}",
                f"API が profile.user を返しませんでした。応答: {data}",
            )
        except Exception as exc:
            return None, str(exc)

    def _mutate(self, method: str, url: str, *, action: str) -> tuple[bool, str]:
        """POST/DELETE an account action; any 2xx counts as success."""

        if not self.token:
            return False, tr(
                "Sign in to Iwara first (Settings → Account).",
                "请先在设置中登录 Iwara 账号。",
                "先にIwaraへログインしてください（設定 → アカウント）。",
            )
        resp = None
        try:
            resp = self.scraper.request(method, url, headers=self._headers(), timeout=30)
            if 200 <= resp.status_code < 300:
                return True, ""
            ok, data, parse_error = _try_response_json(resp, action=action)
            message = _extract_api_message(data) if ok else ""
            return False, f"HTTP {resp.status_code}: {message}" if message else (parse_error or _friendly_http_response(resp))
        except Exception as exc:
            return False, _friendly_network_error(str(exc), action=action)
        finally:
            if resp is not None:
                resp.close()

    def set_liked(self, kind: str, item_id: str, liked: bool) -> tuple[bool, str]:
        """Like (``POST``) or un-like (``DELETE``) a video or image post."""

        kind = "image" if str(kind).strip().lower().startswith("image") else "video"
        item_id = str(item_id or "").strip()
        if not item_id:
            return False, tr("Missing post id", "缺少作品 ID", "投稿IDがありません")
        return self._mutate(
            "POST" if liked else "DELETE",
            f"{BASE_API}/{kind}/{item_id}/like",
            action=tr("like request", "点赞请求", "いいねリクエスト"),
        )

    def set_following(self, user_id: str, following: bool) -> tuple[bool, str]:
        """Follow (``POST``) or unfollow (``DELETE``) a user on the website."""

        user_id = str(user_id or "").strip()
        if not user_id:
            return False, tr("Missing user id", "缺少用户 ID", "ユーザーIDがありません")
        return self._mutate(
            "POST" if following else "DELETE",
            f"{BASE_API}/user/{user_id}/followers",
            action=tr("follow request", "关注请求", "フォローリクエスト"),
        )

    def get_user_videos(
        self,
        user_id: str,
        max_pages: int = 100,
        *,
        known_video_ids: set[str] | None = None,
    ) -> list[dict]:
        """Fetch video stubs for a user, optionally stopping at a known item.

        The user video endpoint is date-sorted.  When a caller already has
        recent IDs, returning the page containing the first known ID is enough
        to discover new items without walking the entire historical archive.
        """
        videos: list[dict] = []
        known_ids = {
            str(video_id or "").strip().casefold()
            for video_id in (known_video_ids or set())
            if str(video_id or "").strip()
        }
        for page in range(max_pages + 1):
            try:
                data = self._get_json(
                    f"{BASE_API}/videos",
                    params={"page": page, "sort": "date", "user": user_id},
                )
                results: list[dict] = data.get("results", [])
                if not results:
                    break
                videos.extend(results)
                if known_ids and any(
                    str(item.get("id", "") or item.get("video_id", "")).strip().casefold()
                    in known_ids
                    for item in results
                    if isinstance(item, dict)
                ):
                    break
            except Exception:
                break
        return videos

    def get_user_following(
        self, user_id: str, max_pages: int = 100
    ) -> tuple[list[dict], str]:
        """Fetch users followed by the given user id."""
        if not user_id:
            return [], tr("Missing user id", "缺少用户 ID", "ユーザーIDがありません")
        last_error = ""
        for root in (BASE_API, ALT_BASE_API):
            users: list[dict] = []
            had_response = False
            for page in range(max_pages + 1):
                try:
                    data = self._get_json(
                        f"{root}/user/{user_id}/following",
                        params={"page": page, "limit": 50},
                    )
                    had_response = True
                except Exception as exc:
                    last_error = f"{root}: {exc}"
                    users = []
                    break

                results = data.get("results", [])
                if not isinstance(results, list) or not results:
                    break
                users.extend(results)
                if len(results) < 50:
                    break
            if had_response:
                return users, ""
        return [], last_error or tr(
            "Failed to fetch following users",
            "获取关注作者失败",
            "フォロー中ユーザーの取得に失敗しました",
        )

    def get_subscribed_videos(
        self,
        *,
        max_pages: int = 100,
        max_results: int = 0,
        known_video_ids: set[str] | None = None,
    ) -> tuple[list[dict], str]:
        """Fetch the logged-in user's subscribed video feed."""
        if not self.token:
            return [], tr(
                "Login is required for subscribed videos",
                "拉取订阅视频需要先登录",
                "購読動画の取得にはログインが必要です",
            )
        return self.get_videos_by_query(
            {"subscribed": "true", "sort": "date"},
            max_pages=max_pages,
            max_results=max_results,
            stop_after_video_ids=known_video_ids,
        )
