"""Show what a link or a selection holds before anything is queued."""

from __future__ import annotations

import json
import os
from typing import Any

from ...config import app_config
from ...i18n import tr
from ..models import TaskStatus
from ..rules import normalize_rule_payload, rule_store
from ..subscription.automation import matches_rule_video
from ..task_metadata import (
    _author_fields_from_user,
    _dict_or_empty,
    _subscription_download_block_from_video_info,
)


HAVE_FILE = "file"  # the video file is on disk
HAVE_HISTORY = "history"  # downloaded once, the file has moved since
HAVE_QUEUED = "queued"  # already in the task list

PREVIEW_KINDS = frozenset(("user", "playlist"))

# Rule switches that need likes / views / tags (unknown for old subscription rows).
_STAT_FILTERS = (
    "filter_min_likes_enabled",
    "filter_min_views_enabled",
    "filter_include_tags_enabled",
    "filter_exclude_tags_enabled",
)


def is_downloadable(item: dict[str, Any]) -> bool:
    """Not here yet in any form, offered by the site, and not rejected by the rule."""

    return not item.get("have") and not item.get("unavailable") and item.get("matches") is not False


def preview_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    """Total, unavailable on the site, stopped by the rule, already here, and downloadable."""

    unavailable = sum(1 for item in items if item.get("unavailable"))
    have = sum(1 for item in items if item.get("have") and not item.get("unavailable"))
    blocked = sum(
        1 for item in items
        if not item.get("unavailable") and not item.get("have") and item.get("matches") is False
    )
    return {
        "total": len(items),
        "unavailable": unavailable,
        "blocked": blocked,
        "have": have,
        "downloadable": sum(1 for item in items if is_downloadable(item)),
    }


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

        result, payload = self._preview_result(rule_id, url=url)
        videos, error = self._listing_videos(url, result)
        if error:
            result["error"] = error
            return result
        result["items"] = self._preview_items(videos, payload)
        return result

    def preview_videos(self, videos: list[dict[str, Any]], *, rule_id: str = "", label: str = "") -> dict[str, Any]:
        """The same preview for videos the caller already has (search results, a selection).

        ``videos`` are API-shaped dicts (``id``, ``title``, ``numLikes``, ...).
        A video with ``_stats_known: False`` leaves likes / views / tag filters
        to the download instead of failing them; ``_unavailable`` marks one the
        site does not offer.
        """

        result, payload = self._preview_result(rule_id, kind="selection", label=label)
        videos = [v for v in videos if isinstance(v, dict) and str(v.get("id", "") or "").strip()]
        if not videos:
            result["error"] = tr("Nothing to download in the selection.", "所选内容中没有可下载的作品。", "選択に保存できる動画がありません。")
            return result
        result["items"] = self._preview_items(videos, payload)
        return result

    def preview_selection(
        self, videos: list[dict[str, Any]], playlist_urls: list[str], *, rule_id: str = "", label: str = "",
    ) -> dict[str, Any]:
        """Selected videos plus every video of the selected playlists, as one preview.

        Playlists are fetched from the site: call it from a worker thread.
        """

        result = self.preview_videos(videos, rule_id=rule_id, label=label) if videos else None
        errors: list[str] = [result["error"]] if result and result["error"] else []
        if result is None or result["error"]:
            result, _payload = self._preview_result(rule_id, kind="selection", label=label)
        seen = {str(item["video_id"]).lower() for item in result["items"]}
        for url in playlist_urls:
            listing = self.preview_listing(url, rule_id=rule_id)
            if listing["error"]:
                errors.append(listing["error"])
            for item in listing["items"]:
                if str(item["video_id"]).lower() not in seen:
                    seen.add(str(item["video_id"]).lower())
                    result["items"].append(item)
        result["error"] = "" if result["items"] else (errors[0] if errors else tr(
            "Nothing to download in the selection.", "所选内容中没有可下载的作品。", "選択に保存できる動画がありません。",
        ))
        return result

    def preview_subscription_items(self, video_ids: list[str], *, rule_id: str = "", label: str = "") -> dict[str, Any]:
        """Preview subscription videos from the local list (no network)."""

        rows = {str(row.get("video_id", "") or ""): row for row in self.subscriptions.get_items_by_video_ids(video_ids)}
        videos = [
            self._subscription_row_video(video_id, rows.get(video_id, {}))
            for video_id in dict.fromkeys(str(v or "").strip() for v in video_ids if str(v or "").strip())
        ]
        return self.preview_videos(videos, rule_id=rule_id, label=label)

    @staticmethod
    def _subscription_row_video(video_id: str, row: dict[str, Any]) -> dict[str, Any]:
        try:
            tags = json.loads(str(row.get("tags_json", "") or "[]"))
        except ValueError:
            tags = []
        return {
            "id": video_id,
            "title": str(row.get("title", "") or video_id),
            "createdAt": str(row.get("published_at", "") or ""),
            "user": {"username": str(row.get("author", "") or ""), "name": str(row.get("username", "") or "")},
            "numLikes": int(row.get("likes", 0) or 0),
            "numViews": int(row.get("views", 0) or 0),
            "numComments": int(row.get("comments", 0) or 0),
            "rating": str(row.get("rating", "") or ""),
            "tags": tags if isinstance(tags, list) else [],
            "file": {"duration": int(row.get("duration", 0) or 0)},
            "_stats_known": bool(row.get("stats_known")),
            "_unavailable": str(row.get("download_reason", "") or "") if row.get("download_state") else "",
        }

    def reclassify_preview(self, preview: dict[str, Any], rule_id: str) -> dict[str, Any]:
        """The same videos under another rule (and with fresh "already here" states); no network."""

        result, payload = self._preview_result(
            rule_id, url=str(preview.get("url", "") or ""), kind=str(preview.get("kind", "") or ""),
            label=str(preview.get("label", "") or ""),
        )
        result["items"] = self._preview_items([item["raw"] for item in preview.get("items") or []], payload)
        return result

    def _preview_result(self, rule_id: str, *, url: str = "", kind: str = "", label: str = ""):
        rule_id = self._normalize_rule_id(rule_id)
        rule = rule_store.find(rule_id) or {}
        payload = self._rule_payload_for_id(rule_id)
        result: dict[str, Any] = {
            "url": url,
            "kind": kind,
            "label": label,
            "error": "",
            "items": [],
            "rule_id": rule_id,
            "rule_name": str(rule.get("name", "") or rule_id),
            # The rule decides whether a confirmed video is fetched or only recorded.
            "mark_only": bool(payload.get("mark_submitted_as_downloaded") or not payload.get("download_video_file", True)),
            "save_dir": os.path.abspath(app_config.download_dir),
        }
        return result, payload

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
            passed, reason = self._preview_match(video, payload)
            unavailable = str(video.get("_unavailable", "") or "") or _subscription_download_block_from_video_info(video)[1]
            items.append({
                **self._preview_fields(video),
                "have": state,
                "have_detail": detail,
                "unavailable": unavailable,
                "matches": passed,
                "match_reason": reason,
                "relative_path": self._preview_relative_path(video, payload),
                "raw": video,
            })
        return items

    @staticmethod
    def _preview_match(video: dict[str, Any], payload: dict[str, Any]) -> tuple[bool | None, str]:
        """``(True | False | None, reason)``; None: the rule needs data only the download will see."""

        if video.get("_stats_known", True):
            passed, reason = matches_rule_video(video, payload)
            return bool(passed), reason
        rule = normalize_rule_payload(payload)
        needs_stats = rule["filter_enabled"] and any(rule[key] for key in _STAT_FILTERS)
        passed, reason = matches_rule_video(video, {**rule, **{key: False for key in _STAT_FILTERS}})
        if not passed:
            return False, reason
        if needs_stats:
            return None, tr(
                "Likes / views / tags are checked against the rule when it downloads",
                "点赞 / 播放 / 标签条件会在下载时按规则核对",
                "いいね・再生数・タグの条件は保存時にルールで確認します",
            )
        return True, ""

    def _have_states(self, video_ids: list[str]) -> dict[str, tuple[str, str]]:
        """Lower-cased id -> (HAVE_*, detail) for videos that are already here in some form."""

        states: dict[str, tuple[str, str]] = {}
        records = self.history.get_records(video_ids)
        index = self._get_existing_file_index()
        with self._lock:
            # A video waiting for confirmation is not "already queued": it is the one being checked.
            queued = {
                vid.lower() for vid in video_ids
                if (task := self._tasks.get(self._task_id_by_video_id.get(vid.lower(), ""))) is not None
                and task.status != TaskStatus.STAGED
            }
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
