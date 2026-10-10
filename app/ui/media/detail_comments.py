"""Comments of the shown post: first page, more pages and reply threads."""

from __future__ import annotations

from typing import Any

from ...i18n import tr
from ..home.workers import CommentsWorker
from .comment_widget import CommentWidget


class DetailCommentsMixin:
    """Comments of the shown post: first page, more pages and reply threads."""

    def _comment_widget(self, comment_id: str) -> "CommentWidget | None":
        for index in range(self._comments_box.count()):
            widget = self._comments_box.itemAt(index).widget()
            if isinstance(widget, CommentWidget) and widget.comment_id == comment_id:
                return widget
        return None

    def _on_comments(self, token: int, rows: object, total: object, error: str):
        if token != self._token:
            return
        self._comments_title.show()
        rows = rows if isinstance(rows, list) else []
        total = total if isinstance(total, int) else None
        self._comment_batches.append((rows, total, error))
        self._append_comments(rows, total, error)

    def _append_comments(self, rows: list[dict[str, Any]], total: int | None, error: str):
        if total is not None:
            self._comment_total = total
        for row in rows:
            widget = CommentWidget(row, self._fetcher, parent=self)
            widget.replies_requested.connect(self._load_replies)
            self._comments_box.addWidget(widget)
            self._comment_count += 1
        if self._comment_total is not None:
            self._comments_title.setText(tr(f"Comments ({self._comment_total})", f"评论（{self._comment_total}）", f"コメント（{self._comment_total}）"))
        if error:
            self._comments_hint.setText(error)
        elif not self._comment_count:
            self._comments_hint.setText(tr("No comments yet", "暂无评论", "コメントはまだありません"))
        else:
            self._comments_hint.setText("")
        more = self._comment_total is not None and self._comment_count < self._comment_total and bool(rows)
        self._more_comments_btn.setVisible(more)
        self._more_comments_btn.setEnabled(True)

    def _load_more_comments(self):
        self._more_comments_btn.setEnabled(False)
        self._comment_page += 1
        self._start_comment_worker(page=self._comment_page, parent="")

    def _load_replies(self, comment_id: str, widget: object):
        self._pending_replies[comment_id] = widget  # type: ignore[assignment]
        self._start_comment_worker(page=0, parent=comment_id)

    def _start_comment_worker(self, *, page: int, parent: str):
        worker = CommentsWorker(self._token, self._kind, self._item_id, page=page, parent=parent)
        worker.comments_ready.connect(self._on_more_comments)
        worker.finished.connect(lambda worker=worker: self._comment_workers.remove(worker) if worker in self._comment_workers else None)
        worker.finished.connect(worker.deleteLater)
        self._comment_workers.append(worker)
        worker.start()

    def _on_more_comments(self, token: int, rows: object, total: object, error: str, parent: str, _page: int):
        if token != self._token:
            return
        rows = rows if isinstance(rows, list) else []
        if parent:
            owner = self._pending_replies.pop(parent, None)
            if owner is not None:
                self._reply_rows[parent] = rows
                owner.add_replies([CommentWidget(row, self._fetcher, nested=True, parent=owner) for row in rows])
            return
        total = total if isinstance(total, int) else None
        self._comment_batches.append((rows, total, error))
        self._append_comments(rows, total, error)
