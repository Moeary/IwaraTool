"""Customizing the Home rows (both interfaces): labels, the row editor's rules and texts."""
from __future__ import annotations

from dataclasses import replace

from ...core.home.feed import (
    BROWSE_SORTS,
    KEYWORD_SORTS,
    MAX_SECTIONS,  # noqa: F401 - re-exported for the editors
    MODE_AUTHOR,
    MODE_BROWSE,
    MODE_KEYWORD,
    MODE_SUBSCRIPTIONS,
    MODE_TAGS,
    HomeSectionSpec,
    new_section_id,
    sanitize_spec,
    sort_label,
)
from ...i18n import tr

MODES = (MODE_BROWSE, MODE_TAGS, MODE_KEYWORD, MODE_AUTHOR, MODE_SUBSCRIPTIONS)


def mode_label(mode: str) -> str:
    return {
        MODE_BROWSE: tr("Hot / newest list", "热门 / 最新列表", "人気・新着リスト"),
        MODE_TAGS: tr("Tag search", "标签搜索", "タグ検索"),
        MODE_KEYWORD: tr("Keyword search", "关键词搜索", "キーワード検索"),
        MODE_AUTHOR: tr("Author's works", "作者作品", "作者の作品"),
        MODE_SUBSCRIPTIONS: tr("My subscription feed", "账号订阅流", "購読フィード"),
    }.get(mode, mode)


def content_choices() -> list[tuple[str, str]]:
    return [("video", tr("Videos", "视频", "動画")), ("image", tr("Images", "图片", "画像"))]


def spec_summary(spec: HomeSectionSpec) -> str:
    """One line describing a row: what it searches and how it is ordered."""

    kind = tr("Images", "图片", "画像") if spec.content == "image" else tr("Videos", "视频", "動画")
    parts = [mode_label(spec.mode)]
    if spec.mode != MODE_SUBSCRIPTIONS:
        parts.append(kind)
    if spec.value:
        parts.append(spec.value)
    if spec.mode != MODE_SUBSCRIPTIONS:
        parts.append(sort_label(spec.sort) if spec.sort else tr("with sort tabs", "带排序标签", "並び替えタブ付き"))
    return " · ".join(parts)


# ── the row editor ───────────────────────────────────────────────────────────


def needs_value(mode: str) -> bool:
    return mode in {MODE_TAGS, MODE_KEYWORD, MODE_AUTHOR}


def shows_sort(mode: str) -> bool:
    return mode != MODE_SUBSCRIPTIONS


def shows_content(mode: str) -> bool:
    return mode not in {MODE_SUBSCRIPTIONS, MODE_TAGS}


def value_caption(mode: str) -> tuple[str, str]:
    """``(label, placeholder)`` of the value field for ``mode``."""
    return {
        MODE_TAGS: (tr("Tags", "标签", "タグ"), tr("e.g. hmv  (comma-separate several)", "例如 hmv（多个标签用逗号分隔）", "例: hmv（複数はカンマ区切り）")),
        MODE_KEYWORD: (tr("Keyword", "关键词", "キーワード"), tr("Text to search for", "要搜索的文字", "検索する文字")),
        MODE_AUTHOR: (tr("Author", "作者", "作者"), tr("Username, e.g. alice", "作者用户名，例如 alice", "ユーザー名 例: alice")),
    }.get(mode, ("", ""))


def mode_hint(mode: str) -> str:
    return {
        MODE_BROWSE: tr(
            "Without a fixed order the row shows Trending / Popular / Newest tabs.",
            "不指定排序时，该栏目会带有 趋势 / 热度 / 最新 三个标签页。",
            "並び順を指定しない場合は トレンド/人気/新着 のタブが付きます。",
        ),
        MODE_TAGS: tr(
            "Uses Iwara tags; localized tag names are matched through the tag dictionary.",
            "使用 Iwara 标签；本地化的标签名会通过标签词典匹配。",
            "Iwaraのタグを使います。翻訳名はタグ辞書で照合されます。",
        ),
        MODE_KEYWORD: tr("Searches Iwara's text index.", "搜索 Iwara 的文本索引。", "Iwaraの全文検索を使います。"),
        MODE_AUTHOR: tr("Shows the newest works of one author.", "显示某位作者的最新作品。", "指定した作者の最新作を表示します。"),
        MODE_SUBSCRIPTIONS: tr("Needs a signed-in Iwara account.", "需要先登录 Iwara 账号。", "Iwaraアカウントへのログインが必要です。"),
    }.get(mode, "")


def sort_choices(mode: str) -> list[tuple[str, str]]:
    """``(sort, label)``; "" is the tabbed row (browse only)."""
    choices = [("", tr("Trending / Popular / Newest tabs", "趋势 / 热度 / 最新 标签页", "トレンド/人気/新着タブ"))] if mode == MODE_BROWSE else []
    sorts = KEYWORD_SORTS if mode == MODE_KEYWORD else BROWSE_SORTS
    return choices + [(sort, sort_label(sort)) for sort in sorts]


def pick_sort(mode: str, previous: str) -> str:
    """Keep the chosen order when the new source has it, else its first one."""
    keys = [key for key, _label in sort_choices(mode)]
    return previous if previous in keys else keys[0]


def build_spec(original: HomeSectionSpec | None, *, mode: str, content: str, title: str, value: str,
               sort: str) -> HomeSectionSpec | None:
    """The edited row, or None when it lacks what to search for."""
    spec = sanitize_spec({
        "id": original.id if original else new_section_id(),
        "mode": mode,
        "content": content or "video",
        "title": title,
        "value": value,
        "sort": sort or "",
        "enabled": original.enabled if original else True,
    })
    if (
        spec is not None
        and original is not None
        and original.is_builtin
        and (spec.mode, spec.content, spec.sort) != (original.mode, original.content, original.sort)
    ):
        # A built-in row turned into something else must stop borrowing the
        # built-in name and its remembered tab.
        spec = replace(spec, id=new_section_id())
    return spec


def move(specs: list[HomeSectionSpec], index: int, delta: int) -> list[HomeSectionSpec]:
    target = index + delta
    if not (0 <= index < len(specs) and 0 <= target < len(specs)):
        return list(specs)
    result = list(specs)
    result.insert(target, result.pop(index))
    return result


# ── texts ────────────────────────────────────────────────────────────────────


def editor_title(editing: bool) -> str:
    return tr("Edit row", "编辑栏目", "欄を編集") if editing else tr("Add a row", "添加栏目", "欄を追加")


def field_labels() -> dict[str, str]:
    return {
        "title": tr("Title", "标题", "タイトル"),
        "title_hint": tr("Leave empty to name it automatically", "留空则自动命名", "空欄なら自動で命名"),
        "mode": tr("Source", "来源", "ソース"),
        "content": tr("Type", "类型", "種類"),
        "sort": tr("Order", "排序", "並び順"),
    }


def missing_value_text() -> str:
    return tr("Enter what to search for.", "请填写要搜索的内容。", "検索する内容を入力してください。")


def customize_title() -> str:
    return tr("Customize Home", "自定义首页", "ホームをカスタマイズ")


def customize_hint() -> str:
    return tr(
        "Tick the rows to show and move them into the order you like. "
        "Any search you can run on the Search page can become a row.",
        "勾选要显示的栏目并调整顺序。搜索页里能搜到的内容，都可以做成首页栏目。",
        "表示する欄を選び、順序を調整します。検索ページで検索できる内容は欄にできます。",
    )


def list_texts() -> dict[str, str]:
    return {
        "add": tr("Add row", "添加栏目", "欄を追加"),
        "edit": tr("Edit", "编辑", "編集"),
        "remove": tr("Remove", "移除", "削除"),
        "up": tr("Move up", "上移", "上へ"),
        "down": tr("Move down", "下移", "下へ"),
        "reset": tr("Reset to default", "恢复默认", "初期設定に戻す"),
        "cancel": tr("Cancel", "取消", "キャンセル"),
        "save": tr("Save", "保存", "保存"),
    }


def empty_note(specs: list[HomeSectionSpec]) -> str:
    if specs and not any(spec.enabled for spec in specs):
        return tr("Nothing selected will show an empty Home page.", "全部取消勾选将显示空白首页。", "すべて外すとホームは空になります。")
    return ""


def reset_question() -> tuple[str, str]:
    return (tr("Reset Home", "恢复默认首页", "ホームを初期化"),
            tr("Replace the list with the default rows?", "用默认栏目替换当前列表？", "一覧を初期の欄に置き換えますか？"))
