"""Rule matching and automatic queueing for newly discovered subscriptions."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from ..i18n import tr
from .rules import BUILTIN_DEFAULT_RULE_ID, normalize_rule_payload, rule_store
from .task_metadata import extract_date_text, normalize_video_tags, split_filter_tags


def matches_rule_metadata(
    *,
    title: str,
    likes: int,
    views: int,
    published_at: str,
    tags: list[Any],
    payload: dict[str, Any],
) -> tuple[bool, str]:
    """Apply the same title/filter semantics used by normal download tasks."""
    rule = normalize_rule_payload(payload)
    normalized_title = str(title or "").casefold()
    title_include = split_filter_tags(rule["title_include"])
    if title_include and not any(term in normalized_title for term in title_include):
        return False, tr(
            f"title did not include any of: {', '.join(title_include)}",
            f"标题未包含任一关键词：{', '.join(title_include)}",
            f"タイトルに指定語句が含まれません：{', '.join(title_include)}",
        )

    title_exclude = split_filter_tags(rule["title_exclude"])
    excluded = [term for term in title_exclude if term in normalized_title]
    if excluded:
        return False, tr(
            f"title matched exclude keywords: {', '.join(excluded)}",
            f"标题命中排除关键词：{', '.join(excluded)}",
            f"タイトルが除外語句に一致：{', '.join(excluded)}",
        )

    if not rule["filter_enabled"]:
        return True, ""
    if rule["filter_min_likes_enabled"] and likes < rule["filter_min_likes"]:
        return False, f"likes {likes} < {rule['filter_min_likes']}"
    if rule["filter_min_views_enabled"] and views < rule["filter_min_views"]:
        return False, f"views {views} < {rule['filter_min_views']}"
    if rule["filter_date_enabled"]:
        date_text = extract_date_text(published_at)
        start = rule["filter_start_date"] or "1970-01-01"
        end = rule["filter_end_date"] or datetime.now().strftime("%Y-%m-%d")
        if not date_text or date_text < start or date_text > end:
            return False, f"date {date_text or '?'} is out of range {start} ~ {end}"

    normalized_tags = normalize_video_tags(tags)
    include_tags = split_filter_tags(rule["filter_include_tags"])
    if rule["filter_include_tags_enabled"] and include_tags:
        if not any(term in normalized_tags for term in include_tags):
            return False, f"no include tags matched ({', '.join(include_tags)})"
    exclude_tags = split_filter_tags(rule["filter_exclude_tags"])
    if rule["filter_exclude_tags_enabled"] and exclude_tags:
        hits = [term for term in exclude_tags if term in normalized_tags]
        if hits:
            return False, f"matched exclude tags ({', '.join(hits)})"
    return True, ""


def matches_rule_video(video_info: dict[str, Any], payload: dict[str, Any]) -> tuple[bool, str]:
    return matches_rule_metadata(
        title=str(video_info.get("title", "") or ""),
        likes=int(video_info.get("numLikes", 0) or 0),
        views=int(video_info.get("numViews", 0) or 0),
        published_at=str(video_info.get("createdAt", "") or ""),
        tags=video_info.get("tags", []) if isinstance(video_info.get("tags"), list) else [],
        payload=payload,
    )


def auto_enqueue_subscription_videos(
    manager,
    video_ids: list[str],
    *,
    rule_id: str,
    priority: int = 10,
) -> dict[str, Any]:
    """Hydrate new videos, match one named rule, and queue successful hits."""
    unique_ids = list(dict.fromkeys(str(value or "").strip() for value in video_ids if str(value or "").strip()))
    selected = rule_store.find(rule_id) or rule_store.find(BUILTIN_DEFAULT_RULE_ID)
    assert selected is not None
    selected_rule_id = str(selected["id"])
    payload = normalize_rule_payload(selected.get("payload"))

    blocked = {
        str(item.get("video_id", "") or "").casefold()
        for item in manager.subscriptions.get_items_by_video_ids(unique_ids)
        if str(item.get("download_state", "") or "")
    }
    candidates = [video_id for video_id in unique_ids if video_id.casefold() not in blocked]
    needs_metadata = bool(
        payload["filter_enabled"]
        or payload["title_include"]
        or payload["title_exclude"]
    )
    matched: list[str] = []
    errors: list[dict[str, str]] = []
    checked = 0
    if not needs_metadata:
        matched = candidates
    else:
        client = manager.create_worker_api_client()
        try:
            for video_id in candidates:
                checked += 1
                try:
                    video_info, error = client.get_video_info(video_id)
                except Exception as exc:
                    video_info, error = None, str(exc)
                if not video_info:
                    errors.append({"video_id": video_id, "error": str(error or "metadata unavailable")})
                    continue
                passed, _reason = matches_rule_video(video_info, payload)
                if passed:
                    matched.append(video_id)
        finally:
            manager.close_worker_api_client(client)

    queued = manager.enqueue_video_ids(
        matched,
        source_label=tr("Subscription automation", "订阅自动化", "購読自動化"),
        priority=priority,
        rule_id=selected_rule_id,
    )
    return {
        "discovered": len(unique_ids),
        "eligible": len(candidates),
        "checked": checked,
        "matched": len(matched),
        "queued": queued,
        "blocked": len(blocked),
        "errors": errors,
        "rule_id": selected_rule_id,
        "rule_name": str(selected.get("name", "") or ""),
    }
