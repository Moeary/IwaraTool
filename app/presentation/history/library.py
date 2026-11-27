"""The library (download history): ways in, filtering, sorting, export and texts (both interfaces).

Record states come from ``record_state.compute_record_state``; everything here
is plain data in and out so the Qt table and the Flet list share one set of
rules.
"""
from __future__ import annotations

import csv
import json
import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any

from ...core.search.models import SearchVideo
from ...core.search.normalize import normalize_video
from ...i18n import tr
from ..media.cards import CardView, video_card
from .record_state import _video_url, compute_record_state, record_search_text

# ── ways in ──────────────────────────────────────────────────────────────────

SCOPE_ALL = "all"
SCOPE_RECENT = "recent"
SCOPE_UNWATCHED = "unwatched"
SCOPE_AUTHOR = "author"
SCOPES = (SCOPE_ALL, SCOPE_RECENT, SCOPE_UNWATCHED, SCOPE_AUTHOR)
RECENT_DAYS = 30

STATE_ALL, STATE_OK, STATE_MOVED = 0, 1, 2  # the state filter (legacy combo indices)

AUTHOR_SORT_COUNT = "count"  # most videos first
AUTHOR_SORT_NAME = "name"  # A → Z in the UI language's order (pinyin for Chinese)

# Search fields, in the legacy combo's order (``record_search_text`` takes the index).
SEARCH_FIELDS = ("all", "state", "title", "author", "quality", "published", "id", "source_url", "path")

# Sort columns; numbers and dates start newest / largest first.
SORT_COLUMNS = ("state", "title", "author", "quality", "published", "likes", "views", "downloaded", "id", "path", "source_url")
DESCENDING_FIRST = frozenset({"published", "likes", "views", "downloaded"})


def scope_choices() -> list[tuple[str, str, str]]:
    """``(key, label, tooltip)`` for the ways in."""
    return [
        (SCOPE_ALL, tr("All", "全部", "すべて"), ""),
        (SCOPE_RECENT, tr("Recently downloaded", "最近下载", "最近の保存"), tr(
            f"Downloaded in the last {RECENT_DAYS} days", f"最近 {RECENT_DAYS} 天内下载的作品", f"直近 {RECENT_DAYS} 日に保存",
        )),
        (SCOPE_UNWATCHED, tr("Unwatched", "未观看", "未視聴"), tr(
            "Files on disk that were never played from IwaraTool",
            "文件仍在、但从未在 IwaraTool 中播放过的作品",
            "IwaraTool で再生したことのない保存済み動画",
        )),
        (SCOPE_AUTHOR, tr("By author", "按作者", "作者別"), ""),
    ]


def state_choices() -> list[tuple[int, str]]:
    return [
        (STATE_ALL, tr("All States", "全部状态", "全状態")),
        (STATE_OK, tr("OK", "正常", "正常")),
        (STATE_MOVED, tr("Moved", "已移走", "移動済み")),
    ]


def search_field_choices() -> list[tuple[str, str]]:
    labels = (
        tr("All Columns", "全部字段", "全列"),
        tr("State", "状态", "状態"),
        tr("Title", "标题", "タイトル"),
        tr("Author", "作者", "作者"),
        tr("Quality", "画质", "画質"),
        tr("Published", "发布日期", "公開日"),
        "ID",
        tr("Source URL", "来源URL", "元URL"),
        tr("Path", "路径", "パス"),
    )
    return list(zip(SEARCH_FIELDS, labels))


def sort_choices() -> list[tuple[str, str]]:
    """The orders offered outside the table (cards, phone list)."""

    return [
        ("downloaded", tr("Downloaded at", "下载时间", "保存日時")),
        ("published", tr("Published", "发布日期", "公開日")),
        ("title", tr("Title", "标题", "タイトル")),
        ("author", tr("Author", "作者", "作者")),
        ("likes", tr("Likes", "点赞", "いいね")),
        ("views", tr("Views", "播放", "再生")),
    ]


def recent_cutoff(now: datetime | None = None) -> str:
    # downloaded_at is SQLite's CURRENT_TIMESTAMP (UTC, "YYYY-MM-DD HH:MM:SS").
    moment = now or datetime.now(timezone.utc)
    return (moment - timedelta(days=RECENT_DAYS)).strftime("%Y-%m-%d %H:%M:%S")


def scope_accepts(record: dict[str, Any], scope: str, state_key: str, cutoff: str, author: str = "") -> bool:
    if scope == SCOPE_RECENT:
        return str(record.get("downloaded_at", "") or "") >= cutoff
    if scope == SCOPE_UNWATCHED:
        return state_key in ("ok", "remote") and not str(record.get("watched_at", "") or "")
    if scope == SCOPE_AUTHOR and author:
        return str(record.get("author", "") or "") == author
    return True


# ── filtering and counting ───────────────────────────────────────────────────


@dataclass(frozen=True)
class LibraryCounts:
    total: int = 0
    visible: int = 0
    ok: int = 0
    moved: int = 0
    remote: int = 0
    unwatched: int = 0


StateOf = Callable[[dict[str, Any]], tuple[str, str, str]]
TagsOf = Callable[[dict[str, Any]], tuple[str, ...]]


def filter_records(
    records: Iterable[dict[str, Any]],
    *,
    state_of: StateOf,
    query: str = "",
    field: int = 0,
    state_filter: int = STATE_ALL,
    scope: str = SCOPE_ALL,
    author: str = "",
    cutoff: str | None = None,
    tags: Iterable[str] = (),
    tags_of: TagsOf | None = None,
) -> tuple[list[dict[str, Any]], LibraryCounts]:
    """The records that pass the scope, state, tag and text filters, plus the page's counts.

    ``tags`` must all be among the record's ``tags_of`` (NFO genres and tags, else the saved Iwara tags);
    with the "all" field the text query matches a tag too.
    """

    query = str(query or "").strip().lower()
    wanted = {str(tag).casefold() for tag in tags if str(tag).strip()}
    cutoff = recent_cutoff() if cutoff is None else cutoff
    visible: list[dict[str, Any]] = []
    total = ok = moved = remote = unwatched = 0
    for record in records:
        total += 1
        label, state_key, detail = state_of(record)
        if state_key in ("ok", "remote"):
            if state_key == "ok":
                ok += 1
            else:
                remote += 1  # on the aria2 host: neither here nor "moved"
            if not str(record.get("watched_at", "") or ""):
                unwatched += 1
        else:
            moved += 1
        if not scope_accepts(record, scope, state_key, cutoff, author):
            continue
        if state_filter == STATE_OK and state_key not in ("ok", "remote"):
            continue
        if state_filter == STATE_MOVED and state_key != "moved":
            continue
        own = tags_of(record) if tags_of is not None and (wanted or (query and field == 0)) else ()
        if wanted and not wanted <= {tag.casefold() for tag in own}:
            continue
        if query and query not in record_search_text(record, field, (label, state_key, detail)) \
                and not (field == 0 and any(query in tag.lower() for tag in own)):
            continue
        visible.append(record)
    return visible, LibraryCounts(total, len(visible), ok, moved, remote, unwatched)


def summary_parts(counts: LibraryCounts) -> list[tuple[str, int]]:
    return [
        (tr("Records", "记录数", "履歴"), counts.total),
        (tr("Visible", "当前显示", "表示"), counts.visible),
        (tr("OK", "正常", "正常"), counts.ok),
        (tr("Moved", "已移走", "移動済み"), counts.moved),
        *([(tr("On NAS", "远端", "リモート"), counts.remote)] if counts.remote else []),
        (tr("Unwatched", "未观看", "未視聴"), counts.unwatched),
    ]


def summary_text(counts: LibraryCounts) -> str:
    return "  ·  ".join(f"{label} {value}" for label, value in summary_parts(counts))


# ── sorting ──────────────────────────────────────────────────────────────────


def _text(value: Any) -> str:
    return str(value or "").lower()


def _number(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def sort_key(record: dict[str, Any], column: str, state: tuple[str, str, str]):
    label, state_key, _detail = state
    if column == "state":
        return (0 if state_key == "ok" else 1, _text(label))
    if column == "title":
        return _text(record.get("title") or record.get("video_id"))
    if column in ("likes", "views"):
        return _number(record.get(column))
    field = {"author": "author", "quality": "quality", "published": "published_at", "downloaded": "downloaded_at",
             "id": "video_id", "path": "file_path"}.get(column)
    if field:
        return _text(record.get(field))
    if column == "source_url":
        return _text(record.get("source_url") or _video_url(record.get("video_id", "")))
    return _text(record.get("downloaded_at"))


def sort_records(records: list[dict[str, Any]], column: str, reverse: bool, *, state_of: StateOf) -> list[dict[str, Any]]:
    return sorted(records, key=lambda record: sort_key(record, column, state_of(record)), reverse=reverse)


# ── authors ──────────────────────────────────────────────────────────────────

_NAME_PART = re.compile(r"\d+|.", re.S)


def chinese_name_key(name: str) -> list[tuple]:
    """Digits by value, then Latin A → Z, then Chinese by pinyin.

    GB2312's 3,755 common characters are encoded in pinyin order, and GBK
    keeps that order (rarer characters follow, by radical and stroke).
    """

    key: list[tuple] = []
    for part in _NAME_PART.findall(unicodedata.normalize("NFKC", name).casefold()):
        if part.isascii() and part.isdigit():
            key.append((0, int(part), part))
        elif ord(part) < 0x2E80:  # Latin, Greek, Cyrillic, punctuation…
            key.append((1, part))
        else:
            try:
                key.append((2, part.encode("gbk")))
            except UnicodeEncodeError:
                key.append((3, part))
    return key


def natural_name_key(name: str) -> list[tuple]:
    """Case-insensitive, numbers by value ("2B" before "10A"): the order without a platform collator."""
    return [(0, int(part), part) if part.isascii() and part.isdigit() else (1, part)
            for part in _NAME_PART.findall(unicodedata.normalize("NFKC", name).casefold())]


def sort_authors(
    counts: dict[str, int],
    order: str,
    *,
    language: str,
    name_sort: Callable[[list[tuple[str, int]]], list[tuple[str, int]]] | None = None,
) -> list[tuple[str, int]]:
    """Named authors with their record counts, by count or by name.

    ``name_sort`` orders the names for languages other than Chinese (the Qt
    interface passes its locale collator); the default is ``natural_name_key``.
    """

    names = [(name, n) for name, n in counts.items() if name]
    if language == "zh":
        named = sorted(names, key=lambda pair: (chinese_name_key(pair[0]), pair[0]))
    elif name_sort is not None:
        named = name_sort(names)
    else:
        named = sorted(names, key=lambda pair: (natural_name_key(pair[0]), pair[0]))
    if order == AUTHOR_SORT_NAME:
        return named
    return sorted(named, key=lambda pair: -pair[1])  # stable: equal counts stay in name order


def author_counts(records: Iterable[dict[str, Any]]) -> Counter:
    return Counter(str(r.get("author", "") or "") for r in records)


def all_authors_text(total: int) -> str:
    return tr(f"All authors ({total})", f"全部作者（{total}）", f"すべての作者（{total}）")


# ── records as videos and rows ───────────────────────────────────────────────


def record_video(record: dict[str, Any]) -> SearchVideo:
    """The post a record was saved from (its stored API answer), or what the record itself knows."""

    try:
        raw = json.loads(str(record.get("raw_json", "") or "") or "{}")
    except (TypeError, ValueError):
        raw = {}
    video = normalize_video(raw) if isinstance(raw, dict) and raw else None
    video_id = str(record.get("video_id", "") or "")
    if video is not None and video.video_id == video_id:
        return video
    return SearchVideo(
        video_id=video_id,
        title=str(record.get("title", "") or video_id),
        author_username=str(record.get("author", "") or ""),
        published_at=str(record.get("published_at", "") or ""),
        likes=_number(record.get("likes")),
        views=_number(record.get("views")),
        duration=float(_number(record.get("duration"))),
        source_url=str(record.get("source_url", "") or ""),
    )


def record_card(record: dict[str, Any]) -> CardView:
    """A poster card with the local thumbnail first (``cover_local``)."""

    video_id = str(record.get("video_id", "") or "")
    return replace(video_card(record_video(record)), key=f"record:{video_id}", history_id=video_id,
                   cover_local=str(record.get("thumbnail_path", "") or ""))


def record_line(record: dict[str, Any]) -> str:
    """"author · downloaded date · quality" under the title of a list row."""
    parts = [str(record.get("author", "") or ""), str(record.get("downloaded_at", "") or "")[:10],
             str(record.get("quality", "") or "")]
    return "  ·  ".join(part for part in parts if part)


def count_text(value: Any) -> str:
    """1.2K / 67.2K for the number columns."""
    from ..media.cards import _format_count

    return _format_count(_number(value))


def column_labels() -> dict[str, str]:
    return {
        "state": tr("State", "状态", "状態"),
        "title": tr("Title", "标题", "タイトル"),
        "author": tr("Author", "作者", "作者"),
        "quality": tr("Quality", "画质", "画質"),
        "published": tr("Published", "发布日期", "公開日"),
        "likes": tr("Likes", "点赞", "いいね"),
        "views": tr("Views", "播放", "再生"),
        "downloaded": tr("Downloaded At", "下载时间", "保存日時"),
        "id": "ID",
        "path": tr("File Path", "文件路径", "ファイルパス"),
        "source_url": tr("Source URL", "来源URL", "元URL"),
    }


def empty_text(filtered: bool) -> str:
    if filtered:
        return tr("No records match the filters.", "没有符合筛选条件的记录。", "条件に合う履歴はありません。")
    return tr("Nothing downloaded yet. Finished downloads appear here.", "还没有下载记录，下载完成的作品会出现在这里。",
              "まだ保存した動画はありません。保存が終わるとここに表示されます。")


def search_placeholder() -> str:
    return tr("Search records…", "搜索记录…", "履歴を検索…")


def reload_tooltip() -> str:
    return tr("Reload DB and refresh file states; no records are deleted", "重新读取数据库并刷新文件状态；不会删除任何记录",
              "DBを再読み込みし状態を更新します。履歴は削除しません")


def export_csv_text() -> str:
    return tr("Export CSV", "导出 CSV", "CSV を書き出す")


def view_choices() -> list[tuple[str, str]]:
    return [("list", tr("List", "列表", "リスト")), ("cover", tr("Covers", "封面", "カバー"))]


# ── export ───────────────────────────────────────────────────────────────────

HISTORY_CSV_FIELDS = (
    "video_id",
    "title",
    "author",
    "quality",
    "published_at",
    "likes",
    "views",
    "downloaded_at",
    "file_path",
    "storage",  # local / remote (saved by aria2 on a NAS / server)
    "remote_path",
    "source_url",
)


def write_history_csv(path: str, records: list[dict[str, Any]]) -> None:
    """Write history rows as UTF-8 CSV with a BOM so Excel opens CJK text."""
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(HISTORY_CSV_FIELDS)
        for record in records:
            row = [str(record.get(field, "") or "") for field in HISTORY_CSV_FIELDS]
            if not row[-1]:
                row[-1] = _video_url(row[0])
            writer.writerow(row)


def export_title() -> str:
    return tr("Export history", "导出历史记录", "履歴を書き出す")


def nothing_to_export_text() -> str:
    return tr("Nothing to export", "没有可导出的记录", "書き出す履歴がありません")


def export_failed_title() -> str:
    return tr("Export failed", "导出失败", "書き出しに失敗")


def exported_notice(count: int, path: str) -> tuple[str, str]:
    return tr("Exported", "已导出", "書き出しました"), tr(
        f"{count} records → {path}", f"{count} 条记录 → {path}", f"{count} 件 → {path}")


# ── record actions: texts ────────────────────────────────────────────────────


def missing_record_text() -> str:
    return tr("History record does not exist", "历史记录不存在", "履歴が存在しません")


def missing_file_text() -> str:
    return tr("File does not exist", "文件不存在", "ファイルが存在しません")


def operation_failed_title() -> str:
    return tr("Operation Failed", "操作失败", "操作失敗")


def rename_texts() -> tuple[str, str, str]:
    """``(title, label, button)`` of the rename dialog."""
    return (tr("Rename File", "重命名文件", "ファイル名を変更"), tr("New file name:", "新的文件名：", "新しいファイル名:"),
            tr("Rename", "重命名", "名前を変更"))


def renamed_title() -> str:
    return tr("Renamed", "已重命名", "名前変更完了")


def remove_title() -> str:
    return tr("Remove Record", "删除记录", "履歴を削除")


def remove_button() -> str:
    return tr("Delete record", "删除记录", "履歴を削除")


def remove_prompt(titles: list[str]) -> str:
    if len(titles) == 1:
        return tr(f"Remove this DB record?\n{titles[0]}", f"删除这条数据库记录？\n{titles[0]}",
                  f"このDB履歴を削除しますか？\n{titles[0]}")
    preview = "\n".join(f"• {title}" for title in titles[:5])
    if len(titles) > 5:
        preview += "\n…"
    return tr(f"Remove {len(titles)} selected DB records?\n{preview}", f"删除选中的 {len(titles)} 条数据库记录？\n{preview}",
              f"選択したDB履歴 {len(titles)} 件を削除しますか？\n{preview}")


def remove_informative() -> str:
    return tr(
        "The local file will not be deleted. Removing this record lets the video be queued again.",
        "不会删除本地文件；删除记录后，该视频之后可以再次入队下载。",
        "ローカルファイルは削除されません。履歴を削除すると再度キューに追加できます。",
    )


def removed_notice(removed: int) -> tuple[str, str]:
    return tr("History records removed", "历史记录已删除", "履歴を削除しました"), tr(
        f"Removed {removed} DB record(s); local files were left untouched.",
        f"已删除 {removed} 条数据库记录；本地文件未被删除。",
        f"DB履歴を {removed} 件削除しました。ローカルファイルは削除していません。",
    )


def undo_text() -> str:
    return tr("Undo", "撤销", "元に戻す")


def clean_selected_texts(count: int) -> tuple[str, str, str, str]:
    """``(title, question, informative, button)`` for cleaning the selected moved records."""
    return (
        tr("Clean Selected Records", "清理选中记录", "選択した履歴を整理"),
        tr(f"Remove {count} selected records whose files are missing or outside the download folder?",
           f"删除选中 {count} 条文件缺失或已移出下载目录的记录？",
           f"ファイル不明または保存先外の選択履歴 {count} 件を削除しますか？"),
        tr("Local files will not be deleted.", "不会删除本地文件。", "ローカルファイルは削除されません。"),
        tr("Clean records", "清理记录", "履歴を整理"),
    )


def cleaned_selected_notice(removed: int) -> tuple[str, str]:
    return tr("Cleanup Finished", "清理完成", "整理完了"), tr(
        f"Removed {removed} selected moved record(s).", f"已清理 {removed} 条选中的失效记录。",
        f"選択した移動済み履歴を {removed} 件整理しました。")


def clean_moved_text() -> str:
    return tr("Clean Moved", "清理已移走记录", "移動済みを削除")


def clean_moved_tooltip() -> str:
    return tr(
        "Delete DB records whose files are missing or outside the download folder",
        "删除数据库中已移走/不在下载文件夹的记录；不会删除本地文件",
        "ファイル不明または保存先外のDB履歴を削除します",
    )


def nothing_to_clean_notice() -> tuple[str, str]:
    return tr("Nothing To Clean", "无需清理", "削除対象なし"), tr(
        "No moved records found", "没有发现已移走记录", "移動済み履歴はありません")


def sync_texts(count: int) -> tuple[str, str, str, str]:
    """``(title, question, informative, button)`` for cleaning every moved record."""
    return (
        tr("Clean Moved Records", "清理已移走记录", "移動済み履歴を削除"),
        tr(f"This will delete {count} DB records for files that are missing or outside the current download folder.",
           f"将删除 {count} 条文件缺失或不在当前下载文件夹内的数据库记录。",
           f"不明または保存先外のDB履歴 {count} 件を削除します。"),
        tr("Local files will not be deleted, but these videos will no longer count as previously saved in history.",
           "不会删除本地文件，但这些视频之后不再被历史库视为已保存；后续可能会重新入队。",
           "ローカルファイルは削除されませんが、履歴上は保存済み扱いではなくなります。"),
        tr("Clean records", "清理记录", "履歴を整理"),
    )


def synced_notice(stats: dict[str, Any]) -> tuple[str, str]:
    kept, removed = stats.get("kept", 0), stats.get("removed", 0)
    return tr("Cleanup Finished", "清理完成", "整理完了"), tr(
        f"Kept {kept}, removed {removed}", f"保留 {kept} 条，删除数据库记录 {removed} 条", f"保持 {kept} 件、DB履歴を {removed} 件削除")


def moved_ids(records: Iterable[dict[str, Any]], state_of: StateOf) -> list[str]:
    return [str(r.get("video_id", "") or "") for r in records if state_of(r)[1] == "moved" and r.get("video_id")]




# ── tags (NFO files and saved Iwara tags) ────────────────────────────────────


def merge_tags(nfo_tags: Iterable[str], saved: Iterable[str]) -> tuple[str, ...]:
    """The NFO's tags first (the user may have edited them), then the saved ones it lacks; case-insensitive."""
    seen: set[str] = set()
    out: list[str] = []
    for tag in (*nfo_tags, *saved):
        text = str(tag or "").strip()
        if text and text.casefold() not in seen:
            seen.add(text.casefold())
            out.append(text)
    return tuple(out)


def tag_counts(records: Iterable[dict[str, Any]], tags_of: TagsOf) -> list[tuple[str, int]]:
    """Every tag with how many records carry it: most used first, then by name."""
    counts: Counter = Counter()
    spelling: dict[str, str] = {}
    for record in records:
        for tag in tags_of(record):
            key = tag.casefold()
            spelling.setdefault(key, tag)
            counts[key] += 1
    return sorted(((spelling[key], count) for key, count in counts.items()), key=lambda item: (-item[1], item[0].casefold()))


def tags_title() -> str:
    return tr("Tags", "标签", "タグ")


def tags_hint() -> str:
    return tr("From the NFO beside each video, else the tags saved with the download.",
              "取自视频旁的 NFO 文件，没有时用下载时保存的标签。",
              "各動画の NFO、無ければダウンロード時に保存したタグから。")


def no_tags_text() -> str:
    return tr("No tags yet: turn on NFO files in Settings > Downloads, or download again.",
              "暂无标签：可在「设置 > 下载」开启 NFO，或重新下载。",
              "タグがありません：設定 > ダウンロードで NFO を有効にしてください。")


def filter_tags_placeholder() -> str:
    return tr("Find a tag", "查找标签", "タグを検索")


def clear_tags_text() -> str:
    return tr("Clear tags", "清除标签", "タグを解除")


def filters_title() -> str:
    return tr("Filter and sort", "筛选与排序", "絞り込みと並び替え")


def plot_label() -> str:
    return tr("Description", "简介", "説明")


def people_label() -> str:
    return tr("People", "人物", "人物")


# ── browsing and playing ─────────────────────────────────────────────────────


def position_text(index: int, total: int) -> str:
    return f"{index + 1} / {total}" if 0 <= index < total else ""


def previous_text() -> str:
    return tr("Previous", "上一个", "前へ")


def next_text() -> str:
    return tr("Next", "下一个", "次へ")


def autoplay_text() -> str:
    return tr("Play next automatically", "自动连播", "自動で次を再生")


def enlarge_text() -> str:
    return tr("View cover", "查看大图", "カバーを拡大")


def play_here_text() -> str:
    return tr("Play here", "在此播放", "ここで再生")


def cannot_play_text() -> str:
    return tr("No playable copy: the file was moved or is on an unreachable aria2 host.",
              "没有可播放的副本：文件已移动，或位于无法访问的 aria2 主机。",
              "再生できるファイルがありません（移動済み、または aria2 ホストに接続できません）。")


# ── managing records ─────────────────────────────────────────────────────────


def edit_details_texts() -> tuple[str, str]:
    """(menu entry / dialog title, save button)."""
    return tr("Edit details", "编辑信息", "情報を編集"), tr("Save", "保存", "保存")


def edit_field_labels() -> dict[str, str]:
    return {
        "title": tr("Title", "标题", "タイトル"),
        "author": tr("Author", "作者", "作者"),
        "quality": tr("Quality", "画质", "画質"),
        "published_at": tr("Published", "发布时间", "公開日"),
    }


def details_saved_text() -> str:
    return tr("Details saved", "信息已保存", "情報を保存しました")


def relocate_text() -> str:
    return tr("Locate file…", "重新定位文件…", "ファイルの場所を指定…")


def relocated_title() -> str:
    return tr("File located", "已重新定位", "ファイルの場所を更新しました")


def watched_texts(watched: bool) -> str:
    return (tr("Mark as unwatched", "标记为未看", "未視聴にする") if watched
            else tr("Mark as watched", "标记为已看", "視聴済みにする"))


def marked_notice(count: int, watched: bool) -> str:
    return (tr(f"{count} marked as watched", f"已将 {count} 项标记为已看", f"{count} 件を視聴済みにしました") if watched
            else tr(f"{count} marked as unwatched", f"已将 {count} 项标记为未看", f"{count} 件を未視聴にしました"))


def cancel_text() -> str:
    return tr("Cancel", "取消", "キャンセル")


def close_text() -> str:
    return tr("Close", "关闭", "閉じる")


def manage_text() -> str:
    return tr("Manage record", "管理记录", "履歴を管理")


def search_field_title() -> str:
    return tr("Search in", "搜索范围", "検索対象")


def state_title() -> str:
    return tr("File state", "文件状态", "ファイルの状態")


def sort_title() -> str:
    return tr("Sort by", "排序方式", "並び替え")


def sort_direction_text(descending: bool) -> str:
    return tr("Descending", "降序", "降順") if descending else tr("Ascending", "升序", "昇順")
