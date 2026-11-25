"""Editing download rules (both interfaces): the form as plain data, list lines, saving and texts.

The form is a flat dict of strings and booleans (what the widgets hold, valid
or not); ``payload_from_form`` checks it and returns a normalized rule payload.
Saving, deleting and making a rule the default also apply it to the global
settings and announce the change on ``signal_bus``, exactly as the old page did.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from ..core.download.paths import validate_filename_template
from ..core.download.template_builder import preview_template
from ..core.rules import (
    BUILTIN_DEFAULT_RULE_ID,
    PAGE_RULE_SCOPES,
    active_rule_id,
    apply_rule_payload,
    current_rule_payload,
    default_rule_payload,
    normalize_rule_payload,
    page_rule_id,
    rule_store,
    set_active_rule_id,
    set_page_rule_choice,
)
from ..i18n import tr
from ..signal_bus import signal_bus
from .rules import DRAFT_RULE_ID, _rule_display_name, _rule_summary, page_scope_name

# ── the form ─────────────────────────────────────────────────────────────────

FORM_TEXTS = (
    "filename_template", "filter_min_likes", "filter_min_views", "filter_start_date", "filter_end_date",
    "filter_include_tags", "filter_exclude_tags", "title_include", "title_exclude",
)
FORM_SWITCHES = (
    "filter_enabled", "filter_min_likes_enabled", "filter_min_views_enabled", "filter_date_enabled",
    "filter_include_tags_enabled", "filter_exclude_tags_enabled", "download_video_file",
    "mark_submitted_as_downloaded", "download_thumbnail", "collect_nfo_info", "record_to_history",
)


def form_from_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    data = normalize_rule_payload(payload)
    form: dict[str, Any] = {key: str(data[key]) for key in FORM_TEXTS}
    form.update({key: bool(data[key]) for key in FORM_SWITCHES})
    return form


def payload_from_form(form: dict[str, Any]) -> dict[str, Any]:
    """A normalized payload; ``ValueError`` (with a readable message) when a field is invalid."""

    try:
        likes = max(0, int(str(form.get("filter_min_likes", "") or "0").strip()))
        views = max(0, int(str(form.get("filter_min_views", "") or "0").strip()))
        start = str(form.get("filter_start_date", "") or "1970-01-01").strip()
        end = str(form.get("filter_end_date", "") or "").strip()
        datetime.strptime(start, "%Y-%m-%d")
        if end:
            datetime.strptime(end, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    template = str(form.get("filename_template", "") or "")
    valid, reason = validate_filename_template(template)
    if not valid:
        raise ValueError(tr(f"Invalid template: {reason}", f"命名规则无效：{reason}", f"無効なテンプレート: {reason}"))
    payload = {key: bool(form.get(key)) for key in FORM_SWITCHES}
    payload.update({
        "filter_min_likes": likes,
        "filter_min_views": views,
        "filter_start_date": start,
        "filter_end_date": end,
        "filter_include_tags": str(form.get("filter_include_tags", "") or ""),
        "filter_exclude_tags": str(form.get("filter_exclude_tags", "") or ""),
        "title_include": str(form.get("title_include", "") or ""),
        "title_exclude": str(form.get("title_exclude", "") or ""),
        "filename_template": template,
    })
    return normalize_rule_payload(payload)


def template_preview_text(template: str) -> str:
    shown = preview_template(template)
    if shown:
        return tr("Preview: ", "预览：", "プレビュー: ") + shown
    return tr("Preview: (template is invalid)", "预览：（规则无效）", "プレビュー: （テンプレートが無効です）")


def history_hint(enabled: bool) -> str:
    if enabled:
        return tr("Downloaded content will be saved to history.", "下载内容会保存到历史。", "ダウンロード内容を履歴に保存します。")
    return tr("Downloaded content will no longer be saved to history.", "不再将下载内容保存到历史。",
              "ダウンロード内容を履歴に保存しません。")


def template_validity(template: str) -> tuple[bool, str, str]:
    """``(valid, title, message)`` for the "Validate" button."""
    valid, reason = validate_filename_template(template)
    if valid:
        return True, tr("Valid template", "命名规则有效", "有効なテンプレート"), tr(
            "The template can be used safely.", "该命名规则可以安全使用。", "この命名テンプレートは安全に使用できます。")
    return False, tr("Invalid template", "命名规则无效", "無効なテンプレート"), reason


def form_labels() -> dict[str, str]:
    return {
        "name": tr("Rule name", "规则名称", "ルール名"),
        "name_hint": tr("e.g. MMD favorites", "例如：MMD 收藏", "例: MMD お気に入り"),
        "summary": tr("Summary", "规则摘要", "概要"),
        "storage": tr("Filename template", "下载命名规则", "ファイル名テンプレート"),
        "template": tr("Full template (editable)", "完整规则（可直接编辑）", "完全なテンプレート（直接編集可）"),
        "validate": tr("Validate", "检验规则", "検証"),
        "validate_tip": tr("Check whether the naming template is valid", "检查命名规则是否有效", "命名テンプレートの有効性を確認"),
        "template_help": tr(
            "Available: {author} {username} {title} {id} {YYYY-MM-DD} {YYYY} {MM} {DD} {quality} {views} {likes} {comments} {duration} {slug} {rating} {date}",
            "可用占位符：{author} {username} {title} {id} {YYYY-MM-DD} {YYYY} {MM} {DD} {quality} {views} {likes} {comments} {duration} {slug} {rating} {date}",
            "使用可能: {author} {username} {title} {id} {YYYY-MM-DD} {YYYY} {MM} {DD} {quality} {views} {likes} {comments} {duration} {slug} {rating} {date}",
        ),
        "presets": tr("Presets", "预设", "プリセット"),
        "insert_field": tr("Insert a field", "插入字段", "フィールドを挿入"),
        "filters": tr("Metadata filters", "元数据筛选", "メタデータフィルター"),
        "filter_enabled": tr("Enable filters", "启用筛选", "フィルターを有効化"),
        "likes": tr("Likes >=", "点赞数 >=", "いいね数 >="),
        "views": tr("Views >=", "播放数 >=", "再生数 >="),
        "dates": tr("Date range", "日期范围", "日付範囲"),
        "start": tr("From (YYYY-MM-DD)", "开始（YYYY-MM-DD）", "開始（YYYY-MM-DD）"),
        "end": tr("To (empty = today)", "结束（留空为至今）", "終了（空欄で今日まで）"),
        "include_tags": tr("Include tags", "包含标签", "含めるタグ"),
        "exclude_tags": tr("Exclude tags", "排除标签", "除外タグ"),
        "tags_hint": tr(
            "Type a Chinese or English tag and choose a candidate; the canonical Iwara tag will be inserted.",
            "输入中文或英文标签并选择候选项，系统会自动填入 Iwara 标准标签。",
            "中国語または英語のタグを入力して候補を選ぶと、Iwara標準タグを自動入力します。",
        ),
        "titles": tr("Title filters", "标题筛选", "タイトルフィルター"),
        "title_include": tr("Include", "包含", "含む"),
        "title_exclude": tr("Exclude", "排除", "除外"),
        "keywords_hint": tr("Any keyword, comma separated", "任一关键词，逗号分隔", "キーワード（カンマ区切り）"),
        "behavior": tr("Download behavior", "下载行为", "保存動作"),
        "download_video": tr("Download video", "下载视频", "動画を保存"),
        "mark_only": tr("Mark only", "仅标记已下载", "マークのみ"),
        "download_thumbnail": tr("Download thumbnail", "下载封面", "サムネイルを保存"),
        "collect_nfo": tr("Write NFO info", "写入 NFO 信息", "NFO 情報を書き出す"),
        "record_history": tr("Write to history", "记录到历史", "履歴へ記録"),
    }


# ── the list ─────────────────────────────────────────────────────────────────


def new_rule_name() -> str:
    return tr("New Rule", "新规则", "新規ルール")


def display_name(rule: dict[str, Any]) -> str:
    """The editor's name field: built-in rules say so."""
    if rule.get("builtin"):
        return tr("Default Download (built in)", "默认下载（内置）", "既定ダウンロード（内蔵）")
    return str(rule.get("name", "") or "")


def rule_kind(rule: dict[str, Any]) -> str:
    if rule.get("id") == DRAFT_RULE_ID:
        return tr("Unsaved draft", "未保存草稿", "未保存の下書き")
    return tr("Built-in default", "内置默认", "内蔵の既定") if rule.get("builtin") else tr("Saved rule", "已保存规则", "保存済み")


def used_by(rule_id: str) -> list[str]:
    return [page_scope_name(scope) for scope in PAGE_RULE_SCOPES if page_rule_id(scope) == rule_id]


def used_by_text(names: list[str]) -> str:
    return tr("Used by: ", "用于：", "使用先：") + tr(", ", "、", "、").join(names) if names else ""


def rule_lines(rule: dict[str, Any], active_id: str) -> tuple[str, str, str]:
    """``(name with ● when default, kind · summary, used by)`` for a list row."""
    mark = "● " if rule.get("id") == active_id else ""
    return (f"{mark}{_rule_display_name(rule) or new_rule_name()}",
            f"{rule_kind(rule)} · {_rule_summary(rule.get('payload') or {})}",
            used_by_text(used_by(str(rule.get("id", "")))))


def list_rules(draft: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Saved rules (built-in first) with an unsaved draft right after the built-in one."""
    rules = list(rule_store.list_available())
    if draft:
        rules.insert(1 if rules else 0, draft)
    return rules


def new_draft() -> dict[str, Any]:
    return {"id": DRAFT_RULE_ID, "name": new_rule_name(), "created_at": "", "updated_at": "", "builtin": False,
            "payload": current_rule_payload()}


def duplicate_draft(name: str, payload: dict[str, Any]) -> dict[str, Any]:
    base = name.replace("（内置）", "").replace("（内蔵）", "").replace(" (built in)", "").strip()
    return {"id": DRAFT_RULE_ID, "name": (base or new_rule_name()) + tr(" copy", " 副本", " コピー"),
            "created_at": "", "updated_at": "", "builtin": False, "payload": dict(payload)}


# ── saving ───────────────────────────────────────────────────────────────────


def save_rule(name: str, payload: dict[str, Any], rule_id: str = "") -> dict[str, Any]:
    """Store the rule (``ValueError`` when invalid); a default rule that was edited is applied again."""
    old_id = "" if rule_id == DRAFT_RULE_ID else rule_id
    rule = rule_store.save(name, payload, old_id or None)
    if old_id and active_rule_id() == old_id:
        apply_rule_payload(rule["payload"])
        signal_bus.download_options_changed.emit()
        signal_bus.active_rule_changed.emit(rule["id"])
    signal_bus.rules_changed.emit()
    return rule


def delete_rule(rule_id: str) -> bool:
    """Remove a saved rule; deleting the default falls back to the built-in rule."""
    if not rule_id or rule_id in (BUILTIN_DEFAULT_RULE_ID, DRAFT_RULE_ID):
        return False
    deleted_active = active_rule_id() == rule_id
    if not rule_store.delete(rule_id):
        return False
    if deleted_active:
        set_active_rule_id(BUILTIN_DEFAULT_RULE_ID)
        apply_rule_payload(default_rule_payload())
        signal_bus.active_rule_changed.emit(BUILTIN_DEFAULT_RULE_ID)
        signal_bus.download_options_changed.emit()
    signal_bus.rules_changed.emit()
    return True


def make_default(rule_id: str, payload: dict[str, Any]) -> None:
    apply_rule_payload(payload)
    set_active_rule_id(rule_id)
    signal_bus.download_options_changed.emit()
    signal_bus.active_rule_changed.emit(rule_id)


# ── texts ────────────────────────────────────────────────────────────────────


def page_title() -> str:
    return tr("Download Rules", "下载规则", "ダウンロードルール")


def intro_text() -> str:
    return tr(
        "One rule combines filters, naming and download behavior. Each page downloads with its own rule; the rule marked ● is the default.",
        "一个规则统一保存筛选、命名和下载行为；每个页面可以使用各自的规则，带 ● 的是默认规则。",
        "フィルター、命名、保存動作を一つにまとめます。ページごとに別のルールを使え、● が既定ルールです。",
    )


def button_texts() -> dict[str, str]:
    return {
        "new": tr("New", "新建", "新規"),
        "duplicate": tr("Duplicate", "复制", "複製"),
        "delete": tr("Delete", "删除", "削除"),
        "default": tr("Set as default", "设为当前默认", "既定に設定"),
        "save": tr("Save rule", "保存规则", "ルールを保存"),
    }


def saved_title() -> str:
    return tr("Rule saved", "规则已保存", "ルールを保存しました")


def default_updated_title() -> str:
    return tr("Default rule updated", "默认规则已更新", "既定ルールを更新しました")


def save_first_notice() -> tuple[str, str]:
    return tr("Save first", "请先保存规则", "先に保存してください"), tr(
        "Save the new rule before making it default.", "新规则保存后才能设为默认。", "新規ルールは保存後に既定へ設定できます。")


def invalid_title() -> str:
    return tr("Invalid rule", "规则无效", "ルールが無効")


def delete_confirmation() -> tuple[str, str]:
    return tr("Delete rule", "删除规则", "ルールを削除"), tr(
        "Delete the selected rule?", "确定删除当前规则？", "選択したルールを削除しますか？")


def unsaved_question() -> tuple[str, str, str]:
    """``(title, message, discard button)`` before leaving an edited rule."""
    return (tr("Unsaved changes", "有未保存的修改", "未保存の変更"),
            tr("Discard the changes to this rule?", "放弃对这条规则的修改吗？", "このルールの変更を破棄しますか？"),
            tr("Discard", "放弃修改", "破棄"))


def page_rules_title() -> str:
    return tr("Rule per page", "各页面使用的规则", "ページごとのルール")


def page_rules_hint() -> str:
    return tr(
        "Downloads started on a page use its rule; \"Follow default\" uses the rule marked ●.",
        "从哪个页面发起下载就用哪个页面的规则；“跟随默认”即使用带 ● 的默认规则。",
        "各ページから始めたダウンロードはそのページのルールを使います。「既定に従う」は ● のルールです。",
    )


def choose_page_rule(scope: str, choice: str) -> None:
    """Remember which rule ``scope`` downloads with and tell the other pickers."""
    set_page_rule_choice(scope, choice)
    signal_bus.page_rule_changed.emit(scope, choice)
