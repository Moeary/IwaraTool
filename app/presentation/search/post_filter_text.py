"""Words and input parsing for the post-search filter (both interfaces)."""
from __future__ import annotations

from ...core.search.filtering import split_search_terms
from ...core.search.post_filter import POST_FILTER_SORTS, PUBLISHED_WITHIN_DAYS, PostFilter
from ...i18n import tr

ORIENTATIONS = ("any", "portrait", "landscape")


def sort_label(key: str) -> str:
    return {
        "server": tr("Keep search order", "保持搜索顺序", "検索順のまま"),
        "likes": tr("Most liked", "点赞最多", "いいね順"),
        "views": tr("Most viewed", "播放最多", "再生数順"),
        "like_ratio": tr("Highest like ratio", "点赞率最高", "いいね率順"),
        "duration_desc": tr("Longest", "时长最长", "長い順"),
        "duration_asc": tr("Shortest", "时长最短", "短い順"),
        "date_desc": tr("Newest", "最新发布", "新しい順"),
        "date_asc": tr("Oldest", "最早发布", "古い順"),
    }[key]


def sort_choices() -> list[tuple[str, str]]:
    return [(sort_label(key), key) for key in POST_FILTER_SORTS]


def within_label(days: int | None) -> str:
    if days is None:
        return tr("Any time", "不限", "指定なし")
    if days == 1:
        return tr("Last 24 hours", "24 小时内", "24時間以内")
    if days == 365:
        return tr("Last year", "一年内", "1年以内")
    return tr(f"Last {days} days", f"{days} 天内", f"{days}日以内")


def within_choices() -> list[tuple[str, int | None]]:
    return [(within_label(days), days) for days in (None, *PUBLISHED_WITHIN_DAYS)]


def orientation_label(key: str) -> str:
    return {
        "any": tr("Any orientation", "不限方向", "指定なし"),
        "portrait": tr("Portrait", "竖屏", "縦長"),
        "landscape": tr("Landscape", "横屏", "横長"),
    }[key]


def number_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def parse_int(text: str) -> int | None:
    text = str(text or "").strip()
    return int(text) if text.isdigit() else None


def parse_float(text: str) -> float | None:
    text = str(text or "").strip().replace(",", ".")
    try:
        return float(text) if text and text != "." else None
    except ValueError:
        return None


def build_filter(
    *,
    min_likes: str = "",
    max_likes: str = "",
    min_views: str = "",
    max_views: str = "",
    min_minutes: str = "",
    max_minutes: str = "",
    within_days: int | None = None,
    min_ratio: str = "",
    exclude_tags: str = "",
    sort: str = "server",
    orientation: str = "any",
) -> PostFilter:
    """A ``PostFilter`` from the form's text fields (empty means no limit)."""

    return PostFilter(
        min_likes=parse_int(min_likes),
        max_likes=parse_int(max_likes),
        min_views=parse_int(min_views),
        max_views=parse_int(max_views),
        min_duration_minutes=parse_float(min_minutes),
        max_duration_minutes=parse_float(max_minutes),
        published_within_days=within_days,
        min_like_ratio_percent=parse_float(min_ratio),
        exclude_tags=split_search_terms(exclude_tags),
        sort=sort if sort in POST_FILTER_SORTS else "server",
        orientation="" if orientation == "any" else orientation,
    )


def title() -> str:
    return tr("Filter loaded results", "筛选已加载的结果", "読み込み済みの結果を絞り込む")


def hint() -> str:
    return tr(
        "The Iwara search API cannot limit these before searching, so they are applied to the results "
        "on the current page and kept while paging. Leave a box empty for no limit. Oreno3D results "
        "join once their Iwara statistics are loaded.",
        "Iwara 搜索接口无法在搜索前限定这些条件，因此会对当前页已加载的结果进行筛选并重新排序，翻页后继续生效。"
        "留空表示不限；Oreno3D 结果需补全 Iwara 数据后才参与筛选。",
        "Iwara の検索 API は検索前にこれらを指定できないため、現在のページで読み込んだ結果に適用し、"
        "ページ移動後も維持します。空欄は制限なしです。Oreno3D の結果は Iwara の統計を取得後に対象になります。",
    )


def orientation_hint() -> str:
    return tr(
        "Uses video resolution: portrait width < height; landscape width > height. Square videos and unknown resolutions only appear with Any orientation.",
        "依据视频分辨率判断：宽 < 高为竖屏，宽 > 高为横屏。方形视频和分辨率未知的视频仅在不限方向时显示。",
        "動画の解像度で判定します。幅 < 高さは縦長、幅 > 高さは横長です。正方形と解像度不明の動画は指定なしの場合のみ表示します。",
    )


def button_text(post_filter: PostFilter) -> str:
    count = post_filter.condition_count()
    label = tr("Filter", "筛选", "絞り込み")
    return f"{label} · {count}" if count else label


def button_tooltip() -> str:
    return tr(
        "Filter and re-sort the loaded results by likes, views, duration and more",
        "按点赞、播放、时长等条件筛选并重新排序已加载的结果",
        "読み込み済みの結果をいいね・再生数・長さなどで絞り込み、並べ替えます",
    )


def validation_message(error: str) -> str:
    """Text for ``PostFilter.validation_error()`` (``""`` when valid)."""

    if not error:
        return ""
    name = {
        "likes": tr("likes", "点赞数", "いいね数"),
        "views": tr("views", "播放数", "再生数"),
        "duration": tr("duration", "时长", "長さ"),
    }.get(error, error)
    return tr(
        f"The minimum {name} is larger than the maximum.",
        f"{name}的最小值大于最大值。",
        f"{name}の最小値が最大値より大きくなっています。",
    )


def label(key: str) -> str:
    """Form labels: likes, views, duration, published, ratio, ratio_hint, ratio_tooltip, exclude,
    exclude_hint, sort, orientation, apply, cancel, clear, min, max."""

    return {
        "likes": tr("Likes", "点赞数", "いいね数"),
        "views": tr("Views", "播放数", "再生数"),
        "duration": tr("Duration (min)", "时长（分钟）", "長さ（分）"),
        "published": tr("Published", "发布时间", "公開日"),
        "ratio": tr("Min like ratio %", "最低点赞率 %", "最低いいね率 %"),
        "ratio_hint": tr("e.g. 5", "如 5", "例: 5"),
        "ratio_tooltip": tr("Likes ÷ views", "点赞数 ÷ 播放数", "いいね数 ÷ 再生数"),
        "exclude": tr("Exclude tags", "排除标签", "除外タグ"),
        "exclude_hint": tr("Comma or space separated", "以逗号或空格分隔", "カンマまたはスペース区切り"),
        "sort": tr("Sort by", "排序", "並び順"),
        "orientation": tr("Video orientation", "视频方向", "動画の向き"),
        "apply": tr("Apply", "应用", "適用"),
        "cancel": tr("Cancel", "取消", "キャンセル"),
        "clear": tr("Clear", "清除条件", "クリア"),
        "min": tr("Min", "最小", "最小"),
        "max": tr("Max", "最大", "最大"),
    }[key]
