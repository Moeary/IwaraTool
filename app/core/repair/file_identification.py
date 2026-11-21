"""Identify the Iwara video behind one scanned file and work out its target name."""

from __future__ import annotations

import os
from typing import Any, Callable

from ...i18n import tr
from .candidate_index import RepairCandidateIndex
from .matching import (
    candidate_match_score,
    extract_iwara_video_id,
    MAX_FILENAME_ID_GUESSES,
    STRIPPED_READING_PENALTY,
    filename_search_text,
    filename_stem,
    filename_video_id_guesses,
    looks_like_video_id,
    pick_repair_candidate,
    repair_target_path,
)
from ..download.task_metadata import _dict_or_empty
from .. import site as sites


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
        stem = filename_stem(path)
        video_id, source, local_candidate, guesses, ambiguous = self._identify_repair_video(
            path, stem, record, candidate_index
        )
        if ambiguous and not guesses:
            item.update(status="ambiguous", message=ambiguous)
            return item
        video_id, source, local_candidate, video_info, error, ambiguous = self._lookup_repair_video(
            path, video_id, source, local_candidate, api_call,
            guesses=guesses, local_ambiguity=ambiguous,
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
        """Video id from the filename or local records.

        Returns ``(id, source, local candidate, unverified id guesses,
        ambiguity message)``. With guesses, the id is empty: the guesses are
        checked online first, and the local title match (or its ambiguity)
        only applies when none of them is confirmed.
        """

        labelled = extract_iwara_video_id(stem)
        if labelled and looks_like_video_id(labelled):
            return labelled, "filename-id", None, [], ""

        if record:
            video_id = str(record.get("video_id", "") or "").strip()
            if video_id:
                return video_id, "history-path", None, [], ""

        matching_ids = candidate_index.ids_in_name(stem)
        if len(matching_ids) == 1:
            return matching_ids[0], "known-id", None, [], ""
        if len(matching_ids) > 1:
            return "", "", None, [], tr(
                "Several known Iwara IDs appear in this filename",
                "文件名中出现了多个已知 Iwara ID",
                "ファイル名に複数の既知 Iwara ID が含まれています",
            )

        # A labelled token that reads like a word ("Iwara ID Challenge") is
        # only a guess, so it cannot block the real bracketed id or a search.
        guesses: list[str] = []
        for guess in ([labelled] if labelled else []) + filename_video_id_guesses(stem):
            if guess.casefold() not in {known.casefold() for known in guesses}:
                guesses.append(guess)
        guesses = guesses[:MAX_FILENAME_ID_GUESSES]

        local_candidate, ambiguous = self._select_local_repair_candidate(path, candidate_index)
        message = tr(
            "The filename matches multiple local subscription/history records",
            "文件名匹配到多个本地订阅/历史记录",
            "ファイル名が複数のローカル購読/履歴に一致します",
        ) if ambiguous else ""
        if guesses:
            return "", "", local_candidate, guesses, message
        if message:
            return "", "", None, [], message
        video_id = str((local_candidate or {}).get("video_id", "") or "").strip()
        if video_id:
            return video_id, "local-metadata", local_candidate, [], ""
        return "", "", None, [], ""

    @staticmethod
    def _repair_title_readings(path: str) -> list[str]:
        """The filename's title as written and without ID-like brackets (``Dance [2024Edition]``)."""

        return list(dict.fromkeys((
            filename_search_text(path, strip_ids=False),
            filename_search_text(path),
        )))

    def _select_local_repair_candidate(self, path: str, candidate_index: RepairCandidateIndex):
        """Title match against local records, each scored by its best title reading."""

        return candidate_index.select_titles(path, self._repair_title_readings(path))

    def _lookup_repair_video(
        self, path: str, video_id: str, source: str, local_candidate, api_call,
        *, guesses=(), local_ambiguity: str = "",
    ):
        """Fetch the video online: by id; else each filename guess, the local
        title match, and finally a title search.

        Returns ``(id, source, candidate, video info, error, ambiguity message)``.
        """

        video_info: dict[str, Any] | None = None
        error = ""
        if not video_id and guesses:
            for guess in guesses:
                # A title word can look like an id. Only a guess the API
                # confirms is used; otherwise the next one, the local title
                # match or a search follows.
                guess_info, guess_error = api_call("get_video_info", guess)
                if guess_info:
                    return guess, "filename-token", None, guess_info, "", ""
                error = guess_error or error
            if local_ambiguity:
                return "", "", None, None, error, local_ambiguity
            video_id = str((local_candidate or {}).get("video_id", "") or "").strip()
            source = "local-metadata" if video_id else ""
            if not video_id:
                local_candidate = None
        if video_id:
            video_info, error = api_call("get_video_info", video_id)
        if not video_id:
            query = filename_search_text(path).strip()
            if query:
                search_items, search_error = api_call(
                    "get_videos_by_query",
                    {"q": query, "sort": "date", "limit": "30"},
                    max_pages=1,
                    max_results=30,
                )
                # A rejected guess says nothing about the file; report the
                # search outcome (a network fault fails the search too).
                error = search_error
                local_candidate, ambiguous = self._select_search_candidate(path, search_items or [])
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
                        error = detail_error or ""
        return video_id, source, local_candidate, video_info, error, ""

    def _select_search_candidate(self, path: str, candidates: list[dict[str, Any]]):
        """Search results, each scored by its best title reading (two exact hits are ambiguous)."""

        return self._select_repair_candidate(path, candidates, titles=self._repair_title_readings(path))

    def _fill_repair_item(
        self, item, path, video_id, source, video_info, error, record, local_candidate,
        *, filename_template: str, output_root: str, move_to_output: bool, site_hint: str = "",
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
        # The API's siteId (or a pasted link's site) names the video's site;
        # the site being browsed is only the fallback.
        meta["source_url"] = str(
            metadata_source.get("source_url", "")
            or sites.video_url(video_id, sites.site_of_item(metadata_source, default=site_hint or None))
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
        titles: list[str] | None = None,
    ) -> tuple[dict[str, Any] | None, bool]:
        author_hint = os.path.basename(os.path.dirname(path))
        ranked: list[tuple[float, dict[str, Any]]] = []
        seen: set[str] = set()
        for candidate in candidates:
            video_id = str(candidate.get("video_id", "") or candidate.get("id", "")).strip()
            if not video_id or video_id.casefold() in seen:
                continue
            seen.add(video_id.casefold())
            # Readings after the first (the name as written) count a little less.
            score = max(
                candidate_match_score(path, candidate, author_hint=author_hint, title=title)
                - (STRIPPED_READING_PENALTY if reading else 0.0)
                for reading, title in enumerate(titles or [None])
            )
            if score > 0:
                ranked.append((score, candidate))
        return pick_repair_candidate(ranked)
