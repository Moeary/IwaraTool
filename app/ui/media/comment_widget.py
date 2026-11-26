"""One comment (with its reply thread) in the detail view."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import AvatarWidget, BodyLabel, CaptionLabel, HyperlinkButton

from ..home.workers import CoverFetcher
from .pixmaps import read_pixmap
from ..style.theme import set_secondary_text
from ...presentation.media.comments import comment_view


class CommentWidget(QWidget):
    """A comment with its author, text and (expandable) replies."""

    replies_requested = Signal(str, object)  # comment id, this widget

    def __init__(self, row: dict[str, Any], fetcher: CoverFetcher, *, nested: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        view = comment_view(row)
        self.comment_id = view.id
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 4, 0, 4)
        outer.setSpacing(10)
        self._avatar = AvatarWidget(self)
        self._avatar.setRadius(16 if nested else 20)
        outer.addWidget(self._avatar, 0, Qt.AlignmentFlag.AlignTop)
        self._avatar_key = view.avatar_key
        self._fetcher = fetcher
        avatar = view.avatar_url
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
        name = BodyLabel(view.author_name, self)
        font = name.font()
        font.setBold(True)
        name.setFont(font)
        header.addWidget(name)
        meta = CaptionLabel(view.meta, self)
        set_secondary_text(meta)
        header.addWidget(meta)
        header.addStretch(1)
        column.addLayout(header)
        body = BodyLabel(self)
        body.setWordWrap(True)
        body.setTextFormat(Qt.TextFormat.RichText)
        body.setText(view.body_html)
        body.setOpenExternalLinks(True)
        body.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        column.addWidget(body)
        self._replies_box = QVBoxLayout()
        self._replies_box.setContentsMargins(0, 4, 0, 0)
        replies = view.reply_count
        self._replies_btn: HyperlinkButton | None = None
        if replies and not nested:
            self._replies_btn = HyperlinkButton("", view.replies_label(), self)
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
