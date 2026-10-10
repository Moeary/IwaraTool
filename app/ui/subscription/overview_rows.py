"""Lazy rows of the subscription overview.

Every subscription owns one slot in the column: a real SourceRow near the
viewport, a spacer of the same height everywhere else."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QSizePolicy, QWidget

from .item_queries import item_state
from .source_row import SourceRow


class OverviewRowsMixin:
    """Build, swap and tear down overview rows as they scroll in and out of range."""

    def _row_signature(self, source: dict[str, Any], items: list[dict[str, Any]]):
        return (
            tuple(source.get(key) for key in self._SIGNATURE_FIELDS),
            tuple((i.get("video_id"), item_state(i), i.get("thumbnail_url"), i.get("thumbnail_path")) for i in items),
        )

    def _make_row(self, source: dict[str, Any], items: list[dict[str, Any]], signature) -> SourceRow:
        row = SourceRow(source, items, self._tile_min, self)
        row.signature = signature
        row.open_requested.connect(self.source_opened)
        row.refresh_requested.connect(self._page._refresh_source_ids_from_view)
        row.menu_requested.connect(self._page._show_source_menu)
        row.video_activated.connect(self._page._show_video_detail)
        row.video_context.connect(self._page._show_video_menu)
        avatar = str(source.get("avatar_path", "") or "")
        if avatar:
            row.set_avatar(avatar)
        return row

    def _average_height(self) -> int:
        heights = [h for h in self._heights.values() if h > 0]
        return round(sum(heights) / len(heights)) if heights else self.ROW_ESTIMATE

    def _slot_height(self, source_id: int) -> int:
        return self._heights.get(source_id) or self._average_height()

    def _new_spacer(self, source_id: int) -> QWidget:
        spacer = QWidget(self)
        spacer.setFixedHeight(self._slot_height(source_id))
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        return spacer

    def _replace_slot(self, source_id: int, new: QWidget, *, keep_view: bool = True):
        """Swap what fills a subscription's place, keeping what the user is looking at still."""

        old = self._slots.get(source_id)
        bar = self._scroll.verticalScrollBar()
        above = old is not None and old.y() + old.height() <= bar.value()
        before = old.height() if old is not None else 0
        if old is None:
            self._column.insertWidget(self._column.count() - 1, new)
        else:
            self._column.replaceWidget(old, new)
            old.hide()
            old.deleteLater()  # no setParent(None): re-parenting a styled tree is slow
        self._slots[source_id] = new
        new.show()
        if keep_view and above:
            # A slot above the viewport changed height: shift the scroll bar by the same
            # amount, otherwise the page would jump under the cursor.
            new_height = new.sizeHint().height() if isinstance(new, SourceRow) else new.height()
            bar.setValue(bar.value() + (new_height - before))

    def _drop_slot(self, source_id: int):
        widget = self._slots.pop(source_id, None)
        if widget is not None:
            self._column.removeWidget(widget)
            widget.hide()
            widget.deleteLater()
        self._rows.pop(source_id, None)
        self._heights.pop(source_id, None)
        self._data.pop(source_id, None)

    def _tear_down_row(self, source_id: int):
        row = self._rows.pop(source_id, None)
        if row is None:
            return
        self._heights[source_id] = max(row.height(), row.sizeHint().height())
        self._replace_slot(source_id, self._new_spacer(source_id))

    def _sync_order(self):
        """Keep the layout in the sorted order even while rows arrive piecemeal."""

        position = 0
        for source_id in sorted(self._slots, key=lambda sid: self._order.get(sid, 1 << 30)):
            widget = self._slots[source_id]
            if self._column.indexOf(widget) != position:
                self._column.removeWidget(widget)
                self._column.insertWidget(position, widget)
            widget.show()
            position += 1

    def _wanted_ids(self, margin: float) -> list[int]:
        """Subscription ids whose slot is within ``margin`` viewport heights of the viewport."""

        viewport = max(self._scroll.viewport().height(), self.MIN_VIEWPORT)
        top = self._scroll.verticalScrollBar().value() - viewport * margin
        bottom = self._scroll.verticalScrollBar().value() + viewport * (1 + margin)
        y = 0
        wanted: list[int] = []
        for source_id in sorted(self._order, key=self._order.get):
            height = self._slots[source_id].height() if source_id in self._rows else self._slot_height(source_id)
            if y + height >= top and y <= bottom:
                wanted.append(source_id)
            y += height + self.ROW_SPACING
        return wanted

    def _realize(self):
        """Queue real rows for what is near the viewport and tear down what is far away."""

        if not self._order:
            return
        near = self._wanted_ids(self.BUILD_MARGIN)
        keep = set(self._wanted_ids(self.KEEP_MARGIN))
        for source_id in [sid for sid in self._rows if sid not in keep]:
            self._tear_down_row(source_id)
        queued = {entry[0] for entry in self._todo}
        for source_id in near:
            if source_id not in self._rows and source_id not in queued and source_id in self._data:
                source, items, signature = self._data[source_id]
                self._todo.append((source_id, source, items, signature))
        if self._todo:
            self._build_rows(sliced=len(self._todo) > self.SYNC_ROWS)
        else:
            self._cover_timer.start()
