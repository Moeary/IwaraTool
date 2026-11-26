"""Pre-checking videos for the waiting list: request payloads, the staging run and its notice.

``stage(manager, job, direct_single=...)`` blocks (lookups, rule checks), so
interfaces call it off the UI thread.  ``staging_notice(result)`` says what
to tell the user; ``None`` means the result needs no notice.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ...core.search.models import SearchVideo
from ...i18n import tr

NOTICE_INFO = "info"
NOTICE_SUCCESS = "success"
NOTICE_WARNING = "warning"
NOTICE_ERROR = "error"


def video_payload(video: SearchVideo, video_id: str = "") -> dict[str, Any]:
    """An API-shaped dict for the pre-check (rule checks, file-name example)."""

    video_id = video_id or video.download_video_id or video.video_id
    raw = video.raw if isinstance(video.raw, dict) else {}
    if video.source_kind == "iwara" and str(raw.get("id", "") or "") == video_id:
        return raw
    return {
        "id": video_id,
        "title": video.title,
        "numLikes": video.likes,
        "numViews": video.views,
        "numComments": video.comments,
        "createdAt": video.published_at,
        "tags": list(video.tags),
        "slug": video.slug,
        "rating": video.rating,
        "user": {"username": video.author_username, "name": video.author_name},
        # Likes / views / tags of another site say nothing about the Iwara rule.
        "_stats_known": video.source_kind == "iwara",
    }


def stage(manager: Any, job: Callable[[], dict[str, Any]], *, direct_single: bool) -> dict[str, Any]:
    """Run the pre-check ``job`` and put its preview on the workbench; errors land in ``error``."""

    try:
        return manager.stage_preview(job(), direct_single=direct_single)
    except Exception as exc:  # a failed lookup must still end the busy state
        return {"error": str(exc), "staged": 0}


def checking_text() -> tuple[str, str]:
    """``(title, content)`` while a pre-check runs."""

    return (
        tr("Checking", "正在预解析", "確認中"),
        tr(
            "Checking what is already here and what the rule keeps…",
            "正在核对已有项、站点可用性与下载规则…",
            "保存済み・利用可否・ルールを確認しています…",
        ),
    )


def review_label() -> str:
    return tr("Review", "去确认", "確認する")


@dataclass(frozen=True, slots=True)
class StagingNotice:
    kind: str  # NOTICE_*
    title: str
    content: str
    review: bool = False  # offer to open the waiting list


def staging_notice(result: dict[str, Any]) -> StagingNotice:
    if result.get("error"):
        return StagingNotice(NOTICE_ERROR, tr("Failed", "失败", "失敗"), str(result["error"]))
    if result.get("direct"):
        title = (
            tr("Marked as downloaded", "已标记为已下载", "保存済みにしました")
            if result.get("marked")
            else tr("Download started", "已开始下载", "ダウンロードを開始しました")
        )
        return StagingNotice(
            NOTICE_SUCCESS,
            title,
            tr("One video, so it did not wait for confirmation.", "单个可下载视频，未进入待确认。", "1 本のため確認を省略しました。"),
        )
    staged = result.get("staged")
    if not staged:
        return StagingNotice(
            NOTICE_WARNING,
            tr("Nothing new", "没有新内容", "新しい項目なし"),
            tr("These videos are already in the task list.", "这些作品已在任务列表中。", "既にタスク一覧にあります。"),
        )
    ready, skip, unavailable = result.get("ready", 0), result.get("skip", 0), result.get("unavailable", 0)
    return StagingNotice(
        NOTICE_SUCCESS,
        tr(f"{staged} waiting for confirmation", f"已加入待确认 {staged} 个", f"確認待ちに {staged} 件"),
        tr(
            f"Ready {ready} · skipped {skip} · cannot download {unavailable}",
            f"可下载 {ready} · 跳过 {skip} · 不能下 {unavailable}",
            f"保存可 {ready}・スキップ {skip}・不可 {unavailable}",
        ),
        review=True,
    )


def confirmed_text(result: dict[str, int]) -> tuple[str, str]:
    """``(title, content)`` after confirming waiting videos."""

    queued, marked, dropped = (int(result.get(key, 0) or 0) for key in ("queued", "marked", "dropped"))
    return tr("Confirmed", "已确认", "確認しました"), tr(
        f"Queued {queued}, marked {marked}; {dropped} skipped rows cleared",
        f"已加入队列 {queued} 个，标记 {marked} 个；清除跳过/不能下的 {dropped} 行",
        f"キュー {queued} 件、記録 {marked} 件。スキップ等 {dropped} 行を削除",
    )


def batch_counts_text(batch: dict[str, Any]) -> str:
    ready, skip, unavailable = (int(batch.get(key, 0) or 0) for key in ("ready", "skip", "unavailable"))
    text = tr(f"{ready} to download · {skip} skipped", f"{ready} 个将下载 · {skip} 个跳过", f"{ready} 件を保存 · {skip} 件スキップ")
    if unavailable:
        text += tr(f" · {unavailable} unavailable", f" · {unavailable} 个无法下载", f" · {unavailable} 件は保存不可")
    return text


def example_path(example: dict[str, str]) -> str:
    """Where the example video of a batch would be saved (``""`` when none would download)."""

    import os

    from ...config import app_config

    relative = str(example.get("relative_path") or "")
    return os.path.join(app_config.download_dir, relative) if relative else ""


def example_text(example: dict[str, str]) -> str:
    if not example:
        return tr("Nothing here would download yet.", "这里还没有将要下载的作品。", "保存予定の動画はまだありません。")
    title, full = example.get("title", ""), example_path(example)
    return tr(f"e.g. {title} → {full}", f"示例：{title} → {full}", f"例: {title} → {full}")
