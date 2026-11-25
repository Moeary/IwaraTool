"""What the comment list of a detail page shows, without a UI framework."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...core.search.normalize import avatar_url
from ...i18n import tr
from .detail_text import format_date, linkify


@dataclass(frozen=True, slots=True)
class CommentView:
    id: str
    author_name: str
    meta: str  # "@username · date"
    body_html: str  # links made clickable
    avatar_key: str
    avatar_url: str
    reply_count: int

    def replies_label(self) -> str:
        count = self.reply_count
        return tr(f"Show {count} replies", f"查看 {count} 条回复", f"{count} 件の返信を表示")


def comment_view(row: dict[str, Any]) -> CommentView:
    comment_id = str(row.get("id") or "")
    user = row.get("user") if isinstance(row.get("user"), dict) else {}
    return CommentView(
        id=comment_id,
        author_name=str(user.get("name") or user.get("username") or "?"),
        meta=f"@{user.get('username', '')} · {format_date(row.get('createdAt', ''))}",
        body_html=linkify(str(row.get("body") or "")),
        avatar_key=str(user.get("id") or comment_id),
        avatar_url=avatar_url(user),
        reply_count=int(row.get("numReplies") or 0),
    )


def comments_title(total: int | None) -> str:
    if total is None:
        return tr("Comments", "评论", "コメント")
    return tr(f"Comments ({total})", f"评论（{total}）", f"コメント（{total}）")


def comments_hint(error: str, shown: int) -> str:
    """The line under the list: the error, "no comments yet", or nothing."""

    if error:
        return error
    if not shown:
        return tr("No comments yet", "暂无评论", "コメントはまだありません")
    return ""


def more_comments_available(total: int | None, shown: int, last_page: list) -> bool:
    return total is not None and shown < total and bool(last_page)
