"""Identify the Iwara video behind one scanned file and work out its target name."""

from __future__ import annotations

import os
from typing import Any, Callable

from ...i18n import tr
from .candidate_index import RepairCandidateIndex
from .matching import (
    candidate_match_score,
    extract_iwara_video_id,
    filename_search_text,
    guess_filename_video_id,
    pick_repair_candidate,
    repair_target_path,
)
from ..task_metadata import _dict_or_empty


class RepairFileIdentificationMixin:
    """Identify the Iwara video behind one scanned file and work out its target name."""

    def _scan_repair_file(
        self,
        path: str,
        *,
        record: dict[str, Any] | None,
        candidate_index: RepairCandidateIndex,
        filename_template: str,
        output_root: str,
        move_to_output: bool,
        api_call: Callable[..., Any] | None = None,
    ) -> dict[str, Any]:
        api_call = api_call or self._api_call
        item = self._repair_scan_item(path, record, output_root, move_to_output)
        stem = os.path.splitext(os.path.basename(path))[0]
        video_id, source, local_candidate, ambiguous = self._identify_repair_video(
            path, stem, record, candidate_index
        )
        if ambiguous:
            item.update(status="ambiguous", message=ambiguous)
            return item
        video_id, source, local_candidate, video_info, error, ambiguous = self._lookup_repair_video(
            path, video_id, source, local_candidate, api_call
        )
        if ambiguous:
            item.update(status="ambiguous", message=ambiguous)
            return item
        return self._fill_repair_item(
            item, path, video_id, source, video_info, error, record, local_candidate,
            filename_template=filename_template, output_root=output_root, move_to_output=move_to_output,
        )

    @staticmethod
    def _repair_scan_item(path: str, record, output_root: str, move_to_output: bool) -> dict[str, Any]:
        return {
            "path": path,
            "original_name": os.path.basename(path),
            "video_id": "",
            "title": "",
            "author": "",
            "published_at": "",
            "target_name": "",
            "target_relative_path": "",
            "target_path": "",
            "status": "not_found",
            "source": "",
            "message": "",
            "metadata": {},
            "cached_meta": dict(record or {}),
            "existing_history": bool(record),
            "output_root": output_root,
            "move_to_output": bool(move_to_output),
        }

    def _identify_repair_video(self, path: str, stem: str, record, candidate_index: RepairCandidateIndex):
        """Video id from the filename or local records: ``(id, source, candidate, ambiguity message)``."""

        video_id = extract_iwara_video_id(stem)
        source = "filename-id" if video_id else ""

        if not video_id and record:
            video_id = str(record.get("video_id", "") or "").strip()
            source = "history-path" if video_id else ""

        if not video_id:
            matching_ids = candidate_index.ids_in_name(stem)
            if len(matching_ids) == 1:
                video_id = matching_ids[0]
                source = "known-id"
            elif len(matching_ids) > 1:
                return "", "", None, tr(
                    "Several known Iwara IDs appear in this filename",
                    "文件名中出现了多个已知 Iwara ID",
                    "ファイル名に複数の既知 Iwara ID が含まれています",
                )

        local_candidate: dict[str, Any] | None = None
        if not video_id:
            local_candidate, ambiguous = candidate_index.select(path)
            if ambiguous:
                return "", "", None, tr(
                    "The filename matches multiple local subscription/history records",
                    "文件名匹配到多个本地订阅/历史记录",
                    "ファイル名が複数のローカル購読/履歴に一致します",
                )
            if local_candidate:
                video_id = str(local_candidate.get("video_id", "") or "").strip()
                source = "local-metadata"

        if not video_id:
            filename_id = guess_filename_video_id(stem)
            if filename_id:
                video_id = filename_id
                source = "filename-token"
        return video_id, source, local_candidate, ""

    def _lookup_repair_video(self, path: str, video_id: str, source: str, local_candidate, api_call):
        """Fetch the video online, searching by title when no id is known.

        Returns ``(id, source, candidate, video info, error, ambiguity message)``.
        """

        video_info: dict[str, Any] | None = None
        error = ""
        if video_id:
            video_info, error = api_call("get_video_info", video_id)
            if not video_info and source == "filename-token":
                # A title can end in an eight-character word. Treat the token
                # as a hint only and fall back to a title search when the API
                # rejects it.
                video_id = ""
                source = ""
        if not video_id:
            query = filename_search_text(path).strip()
            if query:
                search_items, error = api_call(
                    "get_videos_by_query",
                    {"q": query, "sort": "date", "limit": "30"},
                    max_pages=1,
                    max_results=30,
                )
                local_candidate, ambiguous = self._select_repair_candidate(path, search_items or [])
                if ambiguous:
                    message = tr(
                        "Iwara search returned several equally likely videos",
                        "Iwara 搜索返回了多个同样可能的视频",
                        "Iwara検索で同程度に一致する動画が複数あります",
                    )
                    return video_id, source, local_candidate, video_info, error, message
                if local_candidate:
                    video_id = str(local_candidate.get("video_id", "") or local_candidate.get("id", "")).strip()
                    source = "iwara-search"
                    if video_id:
                        video_info, detail_error = api_call("get_video_info", video_id)
                        error = detail_error or error
        return video_id, source, local_candidate, video_info, error, ""

    def _fill_repair_item(
        self, item, path, video_id, source, video_info, error, record, local_candidate,
        *, filename_template: str, output_root: str, move_to_output: bool,
    ) -> dict[str, Any]:
        """Complete ``item`` with metadata and the target path, or explain why it cannot be."""

        cached = record or local_candidate or {}
        if not video_info and not video_id:
            item["message"] = error or tr(
                "No matching Iwara video was found",
                "没有找到匹配的 Iwara 视频",
                "一致する Iwara 動画が見つかりません",
            )
            return item
        if not video_id:
            video_id = str(video_info.get("id", "") or video_info.get("video_id", "") or "").strip() if video_info else ""
        if not video_info and not cached:
            item["video_id"] = video_id
            item["message"] = error or tr(
                "Video metadata is unavailable; skipped",
                "视频元数据不可用，已跳过",
                "動画メタデータを取得できないためスキップしました",
            )
            return item

        metadata_source = video_info or cached
        meta = self._history_meta_from_item(metadata_source, record)
        meta["video_id"] = video_id
        meta["source_url"] = str(
            metadata_source.get("source_url", "")
            or f"https://www.iwara.tv/video/{video_id}"
        )
        user = _dict_or_empty(metadata_source.get("user"))
        author = str(meta.get("author", "") or user.get("username", "") or "").strip()
        title = str(meta.get("title", "") or video_id).strip()
        target_name, target_path = repair_target_path(
            filename_template, meta, path,
            output_root=output_root,
            move_to_output=move_to_output,
        )
        item.update(
            video_id=video_id,
            title=title,
            author=author,
            published_at=str(meta.get("published_at", "") or ""),
            target_name=target_name,
            target_relative_path=target_name,
            target_path=target_path,
            status="ready",
            source=source or "cached-metadata",
            message=(
                tr("Name is already up to date", "文件名已经符合规则", "ファイル名は既に規則どおりです")
                if self._repair_normalize_path(path) == self._repair_normalize_path(target_path)
                else tr("Ready to repair", "可修复", "修復可能")
            ),
            metadata=dict(video_info or {}),
            cached_meta=meta,
            existing_history=bool(record),
        )
        if not video_info and error:
            item["message"] = tr(
                f"Using cached metadata; live lookup failed: {error}",
                f"实时查询失败，使用缓存元数据：{error}",
                f"ライブ取得失敗、キャッシュメタデータを使用: {error}",
            )
        return item

    @staticmethod
    def _select_repair_candidate(
        path: str,
        candidates: list[dict[str, Any]],
    ) -> tuple[dict[str, Any] | None, bool]:
        author_hint = os.path.basename(os.path.dirname(path))
        ranked: list[tuple[float, dict[str, Any]]] = []
        seen: set[str] = set()
        for candidate in candidates:
            video_id = str(candidate.get("video_id", "") or candidate.get("id", "")).strip()
            if not video_id or video_id.casefold() in seen:
                continue
            seen.add(video_id.casefold())
            score = candidate_match_score(path, candidate, author_hint=author_hint)
            if score > 0:
                ranked.append((score, candidate))
        return pick_repair_candidate(ranked)
