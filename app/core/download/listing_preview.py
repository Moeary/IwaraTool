"""Look inside an author or playlist link before anything is queued."""

from __future__ import annotations

import os
from typing import Any

from ...config import app_config
from ...i18n import tr
from ..rules import rule_store
from ..subscription.automation import matches_rule_video
from ..task_metadata import _author_fields_from_user, _dict_or_empty


HAVE_FILE = "file"  # the video file is on disk
HAVE_HISTORY = "history"  # downloaded once, the file has moved since
HAVE_QUEUED = "queued"  # already in the task list

PREVIEW_KINDS = frozenset(("user", "playlist"))


class ListingPreviewMixin:
    """Look inside an author or playlist link before anything is queued."""

    def listing_kind(self, url: str) -> str:
        """``"user"`` or ``"playlist"`` for links worth previewing, else ``""``."""

        parsed = self._parse_iwara_url(str(url or "").strip())
        return parsed[0] if parsed and parsed[0] in PREVIEW_KINDS else ""

    def preview_listing(self, url: str, *, rule_id: str = "") -> dict[str, Any]:
        """Every video behind an author / playlist link, with what is already here and what the rule keeps.

        Runs network requests: call it from a worker thread.
        """

        rule_id = self._normalize_rule_id(rule_id)
        payload = self._rule_payload_for_id(rule_id)
        rule = rule_store.find(rule_id) or {}
        result: dict[str, Any] = {
            "url": url,
            "kind": "",
            "label": "",
            "error": "",
            "items": [],
            "rule_id": rule_id,
            "rule_name": str(rule.get("name", "") or rule_id),
            "save_dir": os.path.abspath(app_config.download_dir),
        }
        videos, error = self._listing_videos(url, result)
        if error:
            result["error"] = error
            return result
        result["items"] = self._preview_items(videos, payload)
        return result

    def _listing_videos(self, url: str, result: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
        parsed = self._parse_iwara_url(str(url or "").strip())
        if not parsed or parsed[0] not in PREVIEW_KINDS:
            return [], tr("Not an author or playlist link.", "不是作者或播放列表链接。", "作者またはプレイリストのリンクではありません。")
        kind, value = parsed
        result["kind"] = kind
        result["label"] = str(value)
        if kind == "user":
            user_id, err = self._api_call("get_user_id", str(value))
            if not user_id:
                return [], tr(f"Could not find the author: {err}", f"找不到该作者：{err}", f"作者が見つかりません: {err}")
            videos = self._api_call("get_user_videos", user_id)
        else:
            videos = self._api_call("get_playlist_videos", str(value))
        videos = [v for v in (videos or []) if isinstance(v, dict) and str(v.get("id", "") or "").strip()]
        if not videos:
            return [], tr(
                "No videos were found behind this link (or the site did not answer).",
                "这个链接下没有找到作品（或站点没有响应）。",
                "このリンクに動画が見つかりませんでした（またはサイトが応答しません）。",
            )
        return videos, ""

    def _preview_items(self, videos: list[dict[str, Any]], payload: dict[str, Any]) -> list[dict[str, Any]]:
        seen: set[str] = set()
        ordered: list[dict[str, Any]] = []
        for video in videos:
            key = str(video.get("id", "") or "").strip().lower()
            if key not in seen:
                seen.add(key)
                ordered.append(video)
        have = self._have_states([str(v.get("id", "") or "").strip() for v in ordered])
        items = []
        for video in ordered:
            video_id = str(video.get("id", "") or "").strip()
            state, detail = have.get(video_id.lower(), ("", ""))
            passed, reason = matches_rule_video(video, payload)
            items.append({
                **self._preview_fields(video),
                "have": state,
                "have_detail": detail,
                "matches": bool(passed),
                "match_reason": reason,
                "relative_path": self._preview_relative_path(video, payload),
                "raw": video,
            })
        return items

    def _have_states(self, video_ids: list[str]) -> dict[str, tuple[str, str]]:
        """Lower-cased id -> (HAVE_*, detail) for videos that are already here in some form."""

        states: dict[str, tuple[str, str]] = {}
        records = self.history.get_records(video_ids)
        index = self._get_existing_file_index()
        with self._lock:
            queued = {vid.lower() for vid in video_ids if vid.lower() in self._task_id_by_video_id}
        for video_id in video_ids:
            key = video_id.lower()
            record = records.get(video_id) or {}
            path = str(record.get("file_path", "") or "")
            if path and os.path.isfile(path):
                states[key] = (HAVE_FILE, path)
            elif index.get(key):
                states[key] = (HAVE_FILE, index[key])
            elif key in queued:
                states[key] = (HAVE_QUEUED, "")
            elif record:
                states[key] = (HAVE_HISTORY, str(record.get("downloaded_at", "") or "")[:16])
        return states

    @staticmethod
    def _preview_fields(video: dict[str, Any]) -> dict[str, Any]:
        author, username = _author_fields_from_user(video.get("user"))
        return {
            "video_id": str(video.get("id", "") or "").strip(),
            "title": str(video.get("title", "") or ""),
            "author": author,
            "username": username,
            "published_at": str(video.get("createdAt", "") or ""),
            "likes": int(video.get("numLikes", 0) or 0),
            "views": int(video.get("numViews", 0) or 0),
        }

    def _preview_relative_path(self, video: dict[str, Any], payload: dict[str, Any]) -> str:
        """Where the file would land under the download folder (quality as preferred)."""

        fields = self._preview_fields(video)
        file_info = _dict_or_empty(video.get("file"))
        try:
            return self._build_output_relative_path(
                title=fields["title"] or fields["video_id"],
                video_id=fields["video_id"],
                author=fields["author"],
                username=fields["username"],
                published_at=fields["published_at"],
                quality=str(app_config.preferred_quality or ""),
                likes=fields["likes"],
                views=fields["views"],
                comments=int(video.get("numComments", 0) or 0),
                duration=int(file_info.get("duration", 0) or 0),
                slug=str(video.get("slug", "") or ""),
                rating=str(video.get("rating", "") or ""),
                filename_template=payload.get("filename_template"),
            )
        except Exception:
            return ""
