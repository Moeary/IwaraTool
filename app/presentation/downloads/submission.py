"""Submitting typed or pasted links to the download queue, without a UI framework.

Authors and playlists (``manager.listing_kind``) are previewed and staged
first (``staging.stage``); plain video links are queued at once.  The queue
calls are quick (resolving happens in the manager's own threads).
"""
from __future__ import annotations

from typing import Any

from ...i18n import tr
from .submitted_urls import split_submitted_urls  # noqa: F401  (re-exported)


def is_mark_only(payload: dict[str, Any]) -> bool:
    """The rule records the videos as downloaded instead of downloading them."""

    return bool(payload.get("mark_submitted_as_downloaded")) or not payload.get("download_video_file", True)


def needs_listing_preview(manager: Any, urls: list[str]) -> bool:
    return len(urls) == 1 and bool(manager.listing_kind(urls[0]))


def queue_links(manager: Any, urls: list[str], *, rule_id: str, mark_only: bool) -> None:
    for url in urls:
        if mark_only:
            manager.add_url_mark_downloaded(url, rule_id=rule_id)
        else:
            manager.add_url(url, rule_id=rule_id)


def no_links_message() -> str:
    return tr("Please input a valid URL first", "请先输入有效的 URL", "先に有効な URL を入力してください")


def submitted_title() -> str:
    return tr("Submitted", "已提交", "送信しました")


def submitted_text(urls: list[str], *, mark_only: bool) -> str:
    prefix = (
        tr("Submitted for marking: ", "已提交标记：", "マーク処理へ送信: ")
        if mark_only
        else tr("Submitted for parsing: ", "已提交解析：", "解析キューに送信: ")
    )
    if len(urls) == 1:
        url = urls[0]
        return prefix + f"{url[:70]}{'…' if len(url) > 70 else ''}"
    return prefix + tr(f"{len(urls)} links", f"{len(urls)} 条链接", f"{len(urls)} 件のリンク")


def listing_busy_text() -> str:
    return tr("Listing every video behind the link…", "正在列出链接中的全部作品并预解析…", "リンク内の動画を一覧にしています…")
