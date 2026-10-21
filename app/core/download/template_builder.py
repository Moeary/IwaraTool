"""Field catalog, presets and (de)composition of filename templates.

The rule editor lets users assemble a template from fields and separators
instead of typing placeholders. A template is represented as a prefix, then
``(field, text-after-field)`` pairs, then the implicit ``.mp4`` extension.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ...i18n import tr
from .paths import FILENAME_TEMPLATE_TOKENS, DownloadPathMixin, validate_filename_template


EXTENSION = ".mp4"
_TOKEN_SPLIT_RE = re.compile(r"(\{[^{}]+\})")

# Sample used for the live preview; mirrors a typical Iwara video.
_SAMPLE = {
    "title": "Sample Title",
    "video_id": "AbCd1234",
    "author": "user154126",
    "username": "Sample Author",
    "published_at": "2025-03-12T08:30:00.000Z",
    "quality": "Source",
    "likes": 1234,
    "views": 56789,
    "comments": 42,
    "duration": 215,
    "slug": "sample-title",
    "rating": "general",
}


@dataclass(frozen=True)
class TemplateField:
    token: str
    label: str
    example: str


def template_fields() -> list[TemplateField]:
    """Fields selectable in the builder, in display order."""

    fields = [
        TemplateField("author", tr("Author ID (account)", "作者账号 ID", "作者アカウントID"), "user154126"),
        TemplateField("username", tr("Author nickname", "作者昵称", "作者ニックネーム"), "Sample Author"),
        TemplateField("title", tr("Video title", "视频标题", "動画タイトル"), "Sample Title"),
        TemplateField("id", tr("Video ID", "视频 ID", "動画ID"), "AbCd1234"),
        TemplateField("YYYY-MM-DD", tr("Date (year-month-day)", "发布日期（年-月-日）", "公開日（年-月-日）"), "2025-03-12"),
        TemplateField("YYYY", tr("Year", "发布年份", "公開年"), "2025"),
        TemplateField("MM", tr("Month", "发布月份", "公開月"), "03"),
        TemplateField("DD", tr("Day", "发布日", "公開日"), "12"),
        TemplateField("quality", tr("Quality", "画质", "画質"), "Source"),
        TemplateField("views", tr("View count", "播放数", "再生数"), "56789"),
        TemplateField("likes", tr("Like count", "点赞数", "いいね数"), "1234"),
        TemplateField("comments", tr("Comment count", "评论数", "コメント数"), "42"),
        TemplateField("duration", tr("Duration (seconds)", "时长（秒）", "長さ（秒）"), "215"),
        TemplateField("slug", tr("URL slug", "链接别名", "URLスラッグ"), "sample-title"),
        TemplateField("rating", tr("Rating", "分级", "レーティング"), "general"),
        TemplateField("date", tr("Date (same as year-month-day)", "发布日期（同年-月-日）", "公開日（年-月-日と同じ）"), "2025-03-12"),
    ]
    assert {field.token for field in fields} == FILENAME_TEMPLATE_TOKENS
    return fields


@dataclass(frozen=True)
class TemplatePreset:
    template: str
    label: str


def template_presets() -> list[TemplatePreset]:
    """Ready-made templates, from simplest to most organised."""

    def preset(template: str, en: str, zh: str, ja: str) -> TemplatePreset:
        return TemplatePreset(template, tr(en, zh, ja))

    return [
        preset(
            "{author}/{YYYY-MM-DD}_{title}_{id}.mp4",
            "Default: author folder / date_title_ID",
            "默认：作者文件夹 / 日期_标题_ID",
            "既定: 作者フォルダー / 日付_タイトル_ID",
        ),
        preset("{title}.mp4", "Title", "标题", "タイトル"),
        preset("{title}_{id}.mp4", "Title + video ID", "标题 + 视频 ID", "タイトル + 動画ID"),
        preset("{id}_{title}.mp4", "Video ID + title", "视频 ID + 标题", "動画ID + タイトル"),
        preset("{title}_{quality}.mp4", "Title + quality", "标题 + 画质", "タイトル + 画質"),
        preset("{id}_{quality}.mp4", "Video ID + quality", "视频 ID + 画质", "動画ID + 画質"),
        preset(
            "{title}_{quality}_{YYYY-MM-DD}.mp4",
            "Title + quality + date",
            "标题 + 画质 + 日期",
            "タイトル + 画質 + 日付",
        ),
        preset("{YYYY-MM-DD}_{title}.mp4", "Date + title", "日期 + 标题", "日付 + タイトル"),
        preset("{YYYY-MM-DD}_{title}_{id}.mp4", "Date + title + ID", "日期 + 标题 + ID", "日付 + タイトル + ID"),
        preset("{author} - {title}.mp4", "Author - title", "作者 - 标题", "作者 - タイトル"),
        preset(
            "{username} - {title} [{id}].mp4",
            "Nickname - title [ID]",
            "作者昵称 - 标题 [ID]",
            "ニックネーム - タイトル [ID]",
        ),
        preset("{author}/{title}.mp4", "Author folder / title", "作者文件夹 / 标题", "作者フォルダー / タイトル"),
        preset(
            "{author}/{title}_{id}.mp4",
            "Author folder / title_ID",
            "作者文件夹 / 标题_ID",
            "作者フォルダー / タイトル_ID",
        ),
        preset(
            "{author}/{title}_{quality}.mp4",
            "Author folder / title_quality",
            "作者文件夹 / 标题_画质",
            "作者フォルダー / タイトル_画質",
        ),
        preset(
            "{username}/{YYYY-MM-DD}_{title}_{id}.mp4",
            "Nickname folder / date_title_ID",
            "昵称文件夹 / 日期_标题_ID",
            "ニックネームフォルダー / 日付_タイトル_ID",
        ),
        preset(
            "{username} ({author})/{title}_{id}.mp4",
            "Nickname (account) folder / title_ID",
            "昵称（账号）文件夹 / 标题_ID",
            "ニックネーム（アカウント）フォルダー / タイトル_ID",
        ),
        preset(
            "{author}/{YYYY}/{title}_{id}.mp4",
            "Author / year folders / title_ID",
            "作者 / 年份文件夹 / 标题_ID",
            "作者 / 年フォルダー / タイトル_ID",
        ),
        preset(
            "{author}/{YYYY}/{MM}/{YYYY-MM-DD}_{title}_{id}.mp4",
            "Author / year / month folders",
            "作者 / 年 / 月文件夹",
            "作者 / 年 / 月フォルダー",
        ),
        preset(
            "{YYYY}/{MM}/{author}_{title}_{id}.mp4",
            "Year / month folders / author_title_ID",
            "年 / 月文件夹 / 作者_标题_ID",
            "年 / 月フォルダー / 作者_タイトル_ID",
        ),
        preset(
            "{YYYY-MM-DD}/{author}/{title}_{id}.mp4",
            "Date folder / author folder / title_ID",
            "日期文件夹 / 作者文件夹 / 标题_ID",
            "日付フォルダー / 作者フォルダー / タイトル_ID",
        ),
        preset(
            "{quality}/{author}/{title}_{id}.mp4",
            "Quality folder / author folder / title_ID",
            "画质文件夹 / 作者文件夹 / 标题_ID",
            "画質フォルダー / 作者フォルダー / タイトル_ID",
        ),
        preset(
            "{rating}/{author}/{title}_{id}.mp4",
            "Rating folder / author folder / title_ID",
            "分级文件夹 / 作者文件夹 / 标题_ID",
            "レーティングフォルダー / 作者フォルダー / タイトル_ID",
        ),
        preset(
            "{author}/{title} [{id}] [{quality}].mp4",
            "Author folder / title [ID] [quality]",
            "作者文件夹 / 标题 [ID] [画质]",
            "作者フォルダー / タイトル [ID] [画質]",
        ),
        preset(
            "{author}/{likes}likes_{title}_{id}.mp4",
            "Author folder / likes first (sortable)",
            "作者文件夹 / 点赞数优先（便于排序）",
            "作者フォルダー / いいね数優先（並べ替え向き）",
        ),
        preset(
            "temp_video/{title}_{id}.mp4",
            "Temporary folder / title_ID",
            "临时文件夹 / 标题_ID",
            "一時フォルダー / タイトル_ID",
        ),
    ]


@dataclass(frozen=True)
class TemplateParts:
    """A template split into a leading text and ``(token, text_after)`` rows."""

    prefix: str
    rows: tuple[tuple[str, str], ...]


def parse_template(template: str) -> TemplateParts | None:
    """Split a template into builder rows, or ``None`` if it has no field.

    Templates that fail validation (unknown field, stray brace) also return
    ``None`` so the builder never silently rewrites text it cannot represent.
    """

    raw = str(template or "").strip()
    if raw.lower().endswith(EXTENSION):
        raw = raw[: -len(EXTENSION)]
    if not validate_filename_template(raw + EXTENSION)[0]:
        return None
    pieces = _TOKEN_SPLIT_RE.split(raw)
    prefix = pieces[0]
    rows: list[tuple[str, str]] = []
    for index in range(1, len(pieces), 2):
        after = pieces[index + 1] if index + 1 < len(pieces) else ""
        rows.append((pieces[index][1:-1], after))
    if not rows:
        return None
    return TemplateParts(prefix, tuple(rows))


def compose_template(prefix: str, rows: list[tuple[str, str]]) -> str:
    """Join builder rows back into a template string ending in ``.mp4``."""

    body = prefix + "".join(f"{{{token}}}{after}" for token, after in rows)
    return body + EXTENSION


def preview_template(template: str) -> str:
    """Render a template with sample data; empty string if it is invalid."""

    if not validate_filename_template(template)[0]:
        return ""
    return DownloadPathMixin()._build_output_relative_path(
        title=_SAMPLE["title"],
        video_id=_SAMPLE["video_id"],
        author=_SAMPLE["author"],
        username=_SAMPLE["username"],
        published_at=_SAMPLE["published_at"],
        quality=_SAMPLE["quality"],
        likes=_SAMPLE["likes"],
        views=_SAMPLE["views"],
        comments=_SAMPLE["comments"],
        duration=_SAMPLE["duration"],
        slug=_SAMPLE["slug"],
        rating=_SAMPLE["rating"],
        filename_template=template,
    )
