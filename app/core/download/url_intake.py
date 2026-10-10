"""Turn submitted Iwara URLs into tasks (or download-history marks)."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

from ...config import app_config
from ...i18n import tr
from ...signal_bus import signal_bus


class UrlIntakeMixin:
    """Turn submitted Iwara URLs into tasks (or download-history marks)."""

    def add_url(self, url: str, *, rule_id: str = ""):
        """Parse URL and enqueue tasks (runs in background thread)."""
        self._parse_executor.submit(self._parse_and_enqueue, url, str(rule_id or ""))

    def add_url_mark_downloaded(self, url: str, *, rule_id: str = ""):
        """Parse URL and mark resolved videos as already downloaded in history."""
        self._parse_executor.submit(self._parse_and_mark_downloaded, url, str(rule_id or ""))

    def _parse_and_enqueue(self, raw: str, rule_id: str = ""):
        url = raw.strip()
        if not url:
            return
        signal_bus.log_message.emit(
            tr(f"Parsing input: {url}", f"解析输入：{url}", f"入力を解析中: {url}")
        )

        parsed = self._parse_iwara_url(url)
        if parsed:
            kind, value = parsed
            if kind == "video":
                signal_bus.log_message.emit(
                    tr(
                        f"[Detected] Video URL -> video_id={value}",
                        f"[识别] 视频链接 → video_id={value}",
                        f"[検出] 動画URL -> video_id={value}",
                    )
                )
            elif kind == "user":
                signal_bus.log_message.emit(
                    tr(
                        f"[Detected] User URL -> username={value}",
                        f"[识别] 用户链接 → username={value}",
                        f"[検出] ユーザーURL -> username={value}",
                    )
                )
            elif kind == "playlist":
                signal_bus.log_message.emit(
                    tr(
                        f"[Detected] Playlist URL -> playlist_id={value}",
                        f"[识别] 播放列表链接 → playlist_id={value}",
                        f"[検出] プレイリストURL -> playlist_id={value}",
                    )
                )
            elif kind == "search":
                signal_bus.log_message.emit(
                    tr(
                        "[Detected] API search URL",
                        "[识别] API 搜索链接",
                        "[検出] API 検索URL",
                    )
                )
            if kind == "video":
                self._enqueue_video_id(value, url, rule_id=rule_id)
                return
            if kind == "user":
                self._enqueue_user(value, rule_id=rule_id)
                return
            if kind == "playlist":
                self._enqueue_playlist(value, rule_id=rule_id)
                return
            if kind == "search":
                self._enqueue_search_query(value, rule_id=rule_id)
                return

        # Treat as raw video ID
        signal_bus.log_message.emit(
            tr(
                f"[Detected] Treat as raw video ID -> {url}",
                f"[识别] 按视频ID处理 → {url}",
                f"[検出] 生の動画IDとして処理 -> {url}",
            )
        )
        self._enqueue_video_id(url, url, rule_id=rule_id)

    def _parse_and_mark_downloaded(self, raw: str, rule_id: str = ""):
        url = raw.strip()
        if not url:
            return
        signal_bus.log_message.emit(
            tr(
                f"[Mark downloaded] parsing input: {url}",
                f"[标记已下载] 解析输入：{url}",
                f"[保存済みマーク] 入力を解析中: {url}",
            )
        )

        items = self._items_to_mark(url)
        if items is None:
            return

        marked = self.mark_video_items_downloaded(items)
        signal_bus.log_message.emit(
            tr(
                f"[Mark downloaded] done: {marked} videos",
                f"[标记已下载] 完成：{marked} 个视频",
                f"[保存済みマーク] 完了: {marked} 件",
            )
        )

    def _items_to_mark(self, url: str) -> list[dict[str, Any]] | None:
        """Video records behind ``url``; None when the input cannot be resolved."""

        parsed = self._parse_iwara_url(url)
        items: list[dict[str, Any]] = []
        if parsed:
            kind, value = parsed
            if kind == "video":
                video_info, err = self._api_call("get_video_info", str(value))
                if video_info:
                    items = [video_info]
                else:
                    signal_bus.log_message.emit(
                        tr(
                            f"[Mark downloaded] video info failed, fallback to id only: {err}",
                            f"[标记已下载] 获取视频信息失败，退回为仅按 ID 标记：{err}",
                            f"[保存済みマーク] 動画情報取得失敗、IDのみで記録: {err}",
                        )
                    )
                    items = [{"video_id": str(value), "source_url": url}]
            elif kind == "user":
                user_id, err = self._api_call("get_user_id", str(value))
                if not user_id:
                    signal_bus.log_message.emit(
                        tr(
                            f"[Mark downloaded] failed to get user id: {err}",
                            f"[标记已下载] 无法获取用户 ID：{err}",
                            f"[保存済みマーク] ユーザーID取得失敗: {err}",
                        )
                    )
                    return
                items = self._api_call("get_user_videos", user_id)
            elif kind == "playlist":
                items = self._api_call("get_playlist_videos", str(value))
            elif kind == "search":
                configured_cap = (
                    max(0, int(app_config.search_limit_count))
                    if app_config.search_limit_enabled
                    else 0
                )
                videos, err = self._api_call(
                    "get_videos_by_query",
                    value,
                    max_results=configured_cap,
                )
                if err:
                    signal_bus.log_message.emit(
                        tr(
                            f"[Mark downloaded] search returned partial result: {err}",
                            f"[标记已下载] 搜索返回部分结果：{err}",
                            f"[保存済みマーク] 検索は部分結果を返しました: {err}",
                        )
                    )
                items = videos
        else:
            video_info, err = self._api_call("get_video_info", url)
            items = [video_info] if video_info else [{"video_id": url, "source_url": url}]
            if err and not video_info:
                signal_bus.log_message.emit(
                    tr(
                        f"[Mark downloaded] video info failed, fallback to id only: {err}",
                        f"[标记已下载] 获取视频信息失败，退回为仅按 ID 标记：{err}",
                        f"[保存済みマーク] 動画情報取得失敗、IDのみで記録: {err}",
                    )
                )
        return items

    def _parse_iwara_url(
        self, raw_url: str
    ) -> tuple[str, str | dict[str, str]] | None:
        """Parse iwara URLs into ('video'|'user'|'playlist'|'search', value)."""
        normalized = raw_url.strip()
        if "iwara.tv" in normalized and "://" not in normalized:
            normalized = f"https://{normalized.lstrip('/')}"

        try:
            parsed = urlparse(normalized)
        except Exception:
            return None

        host = (parsed.netloc or "").lower()
        if host and "iwara.tv" not in host:
            return None

        parts = [p for p in parsed.path.split("/") if p]
        if not parts:
            return None
        lower_parts = [p.lower() for p in parts]

        # Search endpoint, e.g.
        # - https://api.iwara.tv/videos?tags=2d&sort=date
        # - https://www.iwara.tv/videos?tags=2d&sort=date
        if lower_parts[0] == "videos":
            query_raw = parse_qs(parsed.query, keep_blank_values=False)
            query_params = {
                key: values[-1].strip()
                for key, values in query_raw.items()
                if values and values[-1].strip()
            }
            # Keep behavior sane for bare /videos links.
            if not query_params:
                query_params["sort"] = "date"
            return "search", query_params

        def _next_after(key: str) -> str | None:
            try:
                idx = lower_parts.index(key)
            except ValueError:
                return None
            if idx + 1 >= len(parts):
                return None
            return parts[idx + 1]

        video_id = _next_after("video")
        if video_id:
            return "video", video_id

        playlist_id = _next_after("playlist")
        if playlist_id:
            return "playlist", playlist_id

        username = _next_after("user") or _next_after("profile")
        if username:
            return "user", username

        return None
