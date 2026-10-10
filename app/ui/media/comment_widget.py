"""One comment (with its reply thread) in the detail view."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import AvatarWidget, BodyLabel, CaptionLabel, HyperlinkButton

from ...core.search.normalize import avatar_url
from ...i18n import tr
from ..home.workers import CoverFetcher
from .pixmaps import read_pixmap
from ..theme import set_secondary_text
from .detail_text import format_date, linkify


class CommentWidget(QWidget):
    """A comment with its author, text and (expandable) replies."""

    replies_requested = Signal(str, object)  # comment id, this widget

    def __init__(self, row: dict[str, Any], fetcher: CoverFetcher, *, nested: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self.comment_id = str(row.get("id") or "")
        user = row.get("user") if isinstance(row.get("user"), dict) else {}
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 4, 0, 4)
        outer.setSpacing(10)
        self._avatar = AvatarWidget(self)
        self._avatar.setRadius(16 if nested else 20)
        outer.addWidget(self._avatar, 0, Qt.AlignmentFlag.AlignTop)
        self._avatar_key = str(user.get("id") or self.comment_id)
        self._fetcher = fetcher
        avatar = avatar_url(user)
        if avatar:
            cached = fetcher.path_for("avatar", self._avatar_key)
            if cached:
                self._set_avatar(cached)
            else:
                fetcher.cover_ready.connect(self._on_cover)
                fetcher.request([("avatar", self._avatar_key, avatar)])

        column = QVBoxLayout()
        column.setSpacing(2)
        header = QHBoxLayout()
        header.setSpacing(8)
        name = BodyLabel(str(user.get("name") or user.get("username") or "?"), self)
        font = name.font()
        font.setBold(True)
        name.setFont(font)
        header.addWidget(name)
        meta = CaptionLabel(f"@{user.get('username', '')} · {format_date(row.get('createdAt', ''))}", self)
        set_secondary_text(meta)
        header.addWidget(meta)
        header.addStretch(1)
        column.addLayout(header)
        body = BodyLabel(self)
        body.setWordWrap(True)
        body.setTextFormat(Qt.TextFormat.RichText)
        body.setText(linkify(str(row.get("body") or "")))
        body.setOpenExternalLinks(True)
        body.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        column.addWidget(body)
        self._replies_box = QVBoxLayout()
        self._replies_box.setContentsMargins(0, 4, 0, 0)
        replies = int(row.get("numReplies") or 0)
        self._replies_btn: HyperlinkButton | None = None
        if replies and not nested:
            self._replies_btn = HyperlinkButton("", tr(f"Show {replies} replies", f"查看 {replies} 条回复", f"{replies} 件の返信を表示"), self)
            self._replies_btn.clicked.connect(self._on_replies_clicked)
            column.addWidget(self._replies_btn, 0, Qt.AlignmentFlag.AlignLeft)
        column.addLayout(self._replies_box)
        outer.addLayout(column, 1)

    def _on_cover(self, kind: str, key: str, path: str):
        if kind == "avatar" and key == self._avatar_key:
            self._set_avatar(path)

    def _set_avatar(self, path: str):
        pixmap = read_pixmap(path, 256)
        if not pixmap.isNull():
            self._avatar.setImage(pixmap)

    def _on_replies_clicked(self):
        if self._replies_btn is not None:
            self._replies_btn.setEnabled(False)
        self.replies_requested.emit(self.comment_id, self)

    def add_replies(self, widgets: list["CommentWidget"]):
        for widget in widgets:
            self._replies_box.addWidget(widget)
        if self._replies_btn is not None:
            self._replies_btn.hide()
