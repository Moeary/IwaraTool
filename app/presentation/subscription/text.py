"""What the subscription pages say (both interfaces): filters, sorts, source lines, feedback."""
from __future__ import annotations

from typing import Any

from ...i18n import tr
from .helpers import _date_only, _source_sort_label, _source_type_label

SOURCE_SORT_OPTIONS = (
    "title",
    "created_at",
    "last_checked_at",
    "new_count",
    "undownloaded_count",
    "item_count",
    "source_origin",
    "source_key",
)


# ── filters and sorts ────────────────────────────────────────────────────────


def state_filter_choices() -> list[tuple[str, str]]:
    return [
        ("all", tr("All", "全部", "すべて")),
        ("ready", tr("Downloadable", "可下载", "保存可能")),
        ("new", tr("New", "新增", "新規")),
        ("downloaded", tr("Downloaded", "已下载", "保存済み")),
        ("moved", tr("Moved", "已移走", "移動済み")),
        ("queued", tr("Queued", "已入队", "キュー内")),
        ("unavailable", tr("Unavailable", "不可下载", "保存不可")),
    ]


def item_sort_choices() -> list[tuple[str, str]]:
    return [
        ("date_desc", tr("Newest first", "时间倒序", "新しい順")),
        ("date_asc", tr("Oldest first", "时间升序", "古い順")),
        ("new_first", tr("New first", "新增优先", "新規優先")),
        ("title", tr("Title A-Z", "标题 A-Z", "タイトル A-Z")),
    ]


def source_sort_choices() -> list[tuple[str, str]]:
    return [(field, _source_sort_label(field)) for field in SOURCE_SORT_OPTIONS]


def choice_label(choices: list[tuple[str, str]], key: str) -> str:
    return next((label for value, label in choices if value == key), choices[0][1])


def title_search_placeholder() -> str:
    return tr("Search titles…", "搜索标题…", "タイトルを検索…")


def source_search_placeholder() -> str:
    return tr("Search subscriptions…", "搜索订阅…", "購読を検索…")


# ── one source ───────────────────────────────────────────────────────────────


def source_title(source: dict[str, Any]) -> str:
    return str(source.get("title", "") or source.get("source_key", "") or "?")


def source_enabled(source: dict[str, Any]) -> bool:
    return bool(int(source.get("enabled", 1) or 0))


def source_is_author(source: dict[str, Any]) -> bool:
    return str(source.get("source_type", "") or "") == "author"


def new_count_text(count: int) -> str:
    return tr(f"{count} new", f"{count} 个新增", f"新着 {count}")


def paused_text() -> str:
    return tr("Paused", "已停用", "停止中")


def source_subtitle(source: dict[str, Any]) -> str:
    """``@user · Author`` (and "Paused")."""
    key = str(source.get("source_key", "") or "")
    kind = _source_type_label(str(source.get("source_type", "") or ""))
    sub = f"@{key}" if source_is_author(source) else key
    sub = f"{sub}  ·  {kind}" if sub else kind
    if not source_enabled(source):
        sub += "  ·  " + paused_text()
    return sub


def source_stats_text(source: dict[str, Any]) -> str:
    items = int(source.get("item_count", 0) or 0)
    missing = int(source.get("undownloaded_count", 0) or 0)
    parts = [
        tr(f"{items} videos", f"共 {items} 个视频", f"{items} 本"),
        tr(f"{missing} not downloaded", f"{missing} 个未下载", f"未保存 {missing}"),
    ]
    checked = _date_only(str(source.get("last_checked_at", "") or ""))
    if checked:
        parts.append(tr(f"checked {checked}", f"刷新于 {checked}", f"{checked} 更新"))
    return "  ·  ".join(parts)


def overview_stats_text(sources: list[dict[str, Any]]) -> str:
    """The page header: "6 subscriptions · 48 new · 32 not downloaded"."""
    count = len(sources)
    new = sum(int(s.get("new_count", 0) or 0) for s in sources)
    missing = sum(int(s.get("undownloaded_count", 0) or 0) for s in sources)
    return tr(
        f"{count} subscriptions · {new} new · {missing} not downloaded",
        f"订阅 {count} · 新 {new} · 未下载 {missing}",
        f"購読 {count} · 新着 {new} · 未保存 {missing}",
    )


def empty_source_text() -> str:
    return tr("No videos yet — refresh this source.", "还没有视频，刷新一下订阅源吧。", "動画はまだありません。更新してください。")


def no_sources_text() -> str:
    return tr(
        "No subscriptions yet. Add an author or playlist, or import the authors you follow.",
        "还没有订阅。添加作者或播放列表，或导入已关注的作者。",
        "購読はまだありません。作者やプレイリストを追加するか、フォロー中の作者を取り込んでください。",
    )


def refresh_source_tooltip() -> str:
    return tr("Refresh this source", "刷新此订阅源", "この購読元を更新")


def view_all_text() -> str:
    return tr("View all", "查看全部", "すべて表示")


# ── refreshing ───────────────────────────────────────────────────────────────


def refreshing_title() -> str:
    return tr("Refreshing Subscriptions", "正在刷新订阅", "購読を更新中")


def refresh_progress(progress: dict[str, Any]) -> tuple[int, int, str]:
    """``(completed, total, line)`` for a ``refresh_sources`` progress event."""
    total = max(1, int(progress.get("total", 1) or 1))
    index = max(1, min(total, int(progress.get("index", 1) or 1)))
    started = str(progress.get("stage", "") or "") == "started"
    title = str(progress.get("title", "") or "")
    line = tr(
        f"{index}/{total} · {'Refreshing' if started else 'Finished'} {title}",
        f"{index}/{total} · {'正在刷新' if started else '已完成'} {title}",
        f"{index}/{total} · {'更新中' if started else '完了'} {title}",
    )
    return (index - 1 if started else index), total, line


def refresh_finished(summary: dict[str, Any]) -> tuple[str, str, str]:
    """``(kind, title, content)`` of the notice after a refresh."""
    errors = summary.get("errors") or []
    if errors:
        return "warning", tr("Refresh Finished With Errors", "刷新完成但有错误", "一部更新失敗"), str(errors[0].get("error", ""))[:180]
    new, downloaded = summary.get("new", 0), summary.get("downloaded", 0)
    unavailable, total = summary.get("unavailable", 0), summary.get("total", 0)
    return "success", tr("Refresh Finished", "刷新完成", "更新完了"), tr(
        f"New {new}, downloaded locally {downloaded}, unavailable {unavailable}, total {total}",
        f"新增 {new}，本地已下载 {downloaded}，不可下载 {unavailable}，累计 {total}",
        f"新規 {new}、保存済み {downloaded}、保存不可 {unavailable}、合計 {total}",
    )


def refresh_log_line(summary: dict[str, Any]) -> str:
    new, downloaded = summary.get("new", 0), summary.get("downloaded", 0)
    unavailable, total = summary.get("unavailable", 0), summary.get("total", 0)
    return tr(
        f"[Subscriptions] refresh done: new={new}, downloaded={downloaded}, unavailable={unavailable}, total={total}",
        f"[订阅] 刷新完成: 新增={new}, 已下载={downloaded}, 不可下载={unavailable}, 总计={total}",
        f"[購読] 更新完了: 新規={new}, 保存済み={downloaded}, 保存不可={unavailable}, 合計={total}",
    )


def wait_for_refresh_text() -> str:
    return tr("Wait for the current refresh to finish first", "请先等待当前刷新完成", "現在の更新が完了するまでお待ちください")


# ── adding, importing, deleting ──────────────────────────────────────────────


def add_title() -> str:
    return tr("Add Subscription", "添加订阅", "購読を追加")


def add_prompt() -> str:
    return tr("Author username / author URL / playlist URL:", "作者用户名 / 作者主页链接 / 播放列表链接：",
              "作者ユーザー名 / 作者URL / プレイリストURL:")


def add_button() -> str:
    return tr("Add", "添加", "追加")


def cancel_text() -> str:
    return tr("Cancel", "取消", "キャンセル")


def invalid_input_text() -> str:
    return tr("Invalid subscription input", "订阅输入无效", "購読入力が不正です")


def added_notice() -> tuple[str, str]:
    return tr("Subscription Added", "订阅已添加", "購読を追加しました"), tr(
        "Refreshing this source now", "正在立即刷新该订阅源", "この購読元を更新しています")


def import_followed_text() -> str:
    return tr("Import followed authors", "导入已关注作者", "フォロー中の作者を取込")


def import_finished(result: dict[str, Any]) -> tuple[str, str, str]:
    imported = int(result.get("imported", 0) or 0)
    error = str(result.get("error", "") or "")
    if error:
        return "warning", tr("Import Finished", "导入完成", "取込完了"), error
    return "success", tr("Followed Authors Imported", "关注作者已导入", "フォロー作者を取込"), tr(
        f"Synced {imported} authors", f"已同步 {imported} 个作者", f"{imported} 人の作者を同期しました")


def select_sources_first_text() -> str:
    return tr("Select one or more subscriptions first", "请先选择一个或多个订阅", "先に1つ以上の購読を選択してください")


def delete_title() -> str:
    return tr("Delete Subscription", "删除订阅", "購読を削除")


def delete_informative() -> str:
    return tr("Downloaded files and history records will not be deleted.", "不会删除已下载文件和历史记录。",
              "保存済みファイルと履歴は削除されません。")


def delete_confirm_button() -> str:
    return tr("Delete subscription", "删除订阅", "購読を削除")


def deleted_title() -> str:
    return tr("Subscription Deleted", "订阅已删除", "購読を削除しました")


def delete_confirmation(sources: list[dict[str, Any]], ids: list[int]) -> tuple[str, str]:
    """The question and the short summary for deleting ``ids``."""
    by_id = {int(s.get("id", 0) or 0): s for s in sources if int(s.get("id", 0) or 0)}
    names = [
        str(by_id.get(i, {}).get("title", "") or "").strip()
        or str(by_id.get(i, {}).get("source_key", "") or "").strip()
        or f"#{i}"
        for i in ids
    ]
    if len(ids) == 1:
        question = tr(
            f'Delete subscription "{names[0]}" and its cached video list?',
            f'确定删除订阅“{names[0]}”及其缓存视频列表吗？',
            f'購読「{names[0]}」と保存済み動画一覧を削除しますか？',
        )
        return question, names[0]
    preview = "、".join(names[:3])
    if len(names) > 3:
        preview += tr(" and more", "等", "ほか")
    question = tr(
        f"Delete {len(ids)} selected subscriptions and their cached video lists?",
        f"确定删除选中的 {len(ids)} 个订阅及其缓存视频列表吗？",
        f"選択した {len(ids)} 件の購読と保存済み動画一覧を削除しますか？",
    )
    return question, tr(f"Selected: {preview}", f"选中：{preview}", f"選択: {preview}")


def pause_text(enabled: bool) -> str:
    return tr("Pause", "停用", "停止") if enabled else tr("Resume", "启用", "再開")


# ── items: download and marks ────────────────────────────────────────────────


def select_videos_first_text() -> str:
    return tr("Select one or more videos first", "请先选中右侧列表里的一个或多个视频", "先に右側リストで動画を選択してください")


def select_moved_first_text() -> str:
    return tr("Select one or more moved videos first", "请先选中一个或多个已移走的视频", "先に移動済み動画を選択してください")


def no_downloadable_text() -> str:
    return tr("No downloadable videos in current selection", "当前选择没有可下载视频", "保存可能な動画がありません")


def still_checking_text() -> str:
    return tr("Still checking the previous selection", "上一批仍在预解析中", "前の選択を確認中です")


def default_batch_label() -> str:
    return tr("Subscription videos", "订阅作品", "購読の動画")


def marked_notice(count: int) -> tuple[str, str]:
    return tr("Marked", "已标记", "マーク完了"), tr(
        f"Marked {count} videos as downloaded/moved",
        f"已将 {count} 个视频标记为已下载（移走）",
        f"{count} 件を保存済み（移動済み）にしました",
    )


def restored_notice(count: int) -> tuple[str, str]:
    return tr("Restored", "已还原", "解除完了"), tr(
        f"Restored {count} moved records",
        f"已还原 {count} 个已移走记录",
        f"移動済み記録を {count} 件解除しました",
    )


def mark_seen_text() -> str:
    return tr("Mark as seen", "标记为已读", "既読にする")


def mark_moved_text() -> str:
    return tr("Mark as downloaded (moved)", "标记为已下载（移走）", "保存済み（移動済み）にする")


def restore_moved_text() -> str:
    return tr("Undo moved mark", "还原已移走标记", "移動済みを解除")


def download_new_text(count: int) -> str:
    return tr(f"Download new ({count})", f"下载新增（{count}）", f"新着を保存（{count}）")
