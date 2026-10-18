"""Display names and one-line summaries of download rules."""

from __future__ import annotations

from typing import Any

from ...core.rules import normalize_rule_payload
from ...i18n import tr


DRAFT_RULE_ID = "__draft_rule__"


def _rule_summary(payload: dict[str, Any]) -> str:
    data = normalize_rule_payload(payload)
    actions = [tr("mark only", "仅标记", "マークのみ") if data["mark_submitted_as_downloaded"] else tr("video", "视频", "動画")]
    if data["download_thumbnail"]:
        actions.append(tr("cover", "封面", "サムネイル"))
    if data["collect_nfo_info"]:
        actions.append("NFO")
    if not data["record_to_history"]:
        actions.append(tr("no history", "不写历史", "履歴なし"))
    filters: list[str] = []
    if data["filter_enabled"]:
        if data["filter_min_likes_enabled"]:
            filters.append(f"{tr('likes', '点赞', 'いいね')}≥{data['filter_min_likes']}")
        if data["filter_min_views_enabled"]:
            filters.append(f"{tr('views', '播放', '再生')}≥{data['filter_min_views']}")
        if data["filter_date_enabled"]:
            filters.append(tr("date", "日期", "日付"))
        if data["filter_include_tags_enabled"] or data["filter_exclude_tags_enabled"]:
            filters.append(tr("tags", "标签", "タグ"))
    if data["title_include"] or data["title_exclude"]:
        filters.append(tr("title keywords", "标题关键词", "タイトル語句"))
    return f"{' + '.join(actions)} · {' · '.join(filters) if filters else tr('no filters', '无筛选', 'フィルターなし')}"


def page_scope_name(scope: str) -> str:
    """Name of a page that has its own download rule."""
    names = {
        "subscription": tr("Subscriptions", "订阅页", "購読"),
        "search": tr("Search", "搜索页", "検索"),
        "detail": tr("Video details", "视频详情页", "動画詳細"),
        "home": tr("Home", "首页", "ホーム"),
        "downloads": tr("Download links", "下载页链接", "ダウンロードリンク"),
    }
    return names.get(scope, scope)


def _rule_display_name(rule: dict[str, Any]) -> str:
    if rule.get("builtin"):
        return tr("Default Download", "默认下载", "既定ダウンロード")
    return str(rule.get("name", "") or "")
