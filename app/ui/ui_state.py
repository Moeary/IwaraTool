"""Small helpers for persistent UI geometry."""
from __future__ import annotations

import json

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import QApplication, QHeaderView, QSplitter, QTableWidget

from ..config import app_config


def restore_splitter_sizes(splitter: QSplitter, key: str, default_sizes: list[int]):
    raw = str(app_config.get_ui_value(key, "") or "")
    sizes = _parse_int_list(raw)
    splitter.setSizes(sizes if len(sizes) == len(default_sizes) else default_sizes)


def connect_splitter_saver(splitter: QSplitter, key: str):
    splitter.splitterMoved.connect(lambda *_args: app_config.set_ui_value(key, ",".join(str(v) for v in splitter.sizes())))


def _user_dragging() -> bool:
    return QApplication.mouseButtons() != Qt.MouseButton.NoButton


def _looks_reset(table: QTableWidget, widths: list[int]) -> bool:
    """Every visible column at the header's default size: Qt reset them, nobody chose that."""

    header = table.horizontalHeader()
    default = header.defaultSectionSize()
    shown = [
        (header.visualIndex(col) if col < table.columnCount() else col, w)
        for col, w in enumerate(widths)
        if w > 0 and (col >= table.columnCount() or not table.isColumnHidden(col))  # 0: a hidden column
    ]
    shown = [w for _visual, w in sorted(shown)][:-1]  # the last column is stretched to the edge
    return len(shown) > 1 and all(w == default for w in shown)


def restore_table_widths(table: QTableWidget, key: str, default_widths: dict[int, int]):
    raw = str(app_config.get_ui_value(key, "") or "")
    widths = _parse_int_list(raw)
    if _looks_reset(table, widths):
        widths = []  # saved after Qt had reset the table: worthless, use the defaults
    wanted = {}
    for col, default in default_widths.items():
        wanted[col] = widths[col] if 0 <= col < len(widths) and widths[col] > 0 else default
        table.setColumnWidth(col, wanted[col])
    _StickyWidths.attach(table, wanted)


def keep_table_widths(table: QTableWidget, widths: dict[int, int]):
    """Set column widths that survive Qt's reset on re-style / reparent (no saving)."""

    for column, width in widths.items():
        table.setColumnWidth(column, width)
    _StickyWidths.attach(table, widths)


class _StickyWidths(QObject):
    """Put a table's column widths back after Qt resets them.

    A header with a style sheet goes back to the default section size whenever
    the table is re-styled: moved into a card, a stack or the main window, or
    on a theme switch.  The intended widths are kept here (and follow the
    user's own resizing) and re-applied after such an event.
    """

    _EVENTS = (QEvent.Type.StyleChange, QEvent.Type.ParentChange, QEvent.Type.Show)

    @classmethod
    def attach(cls, table: QTableWidget, wanted: dict[int, int]):
        sticky = getattr(table, "_ui_sticky_widths", None)
        if sticky is None:
            sticky = cls(table)
            table._ui_sticky_widths = sticky
        sticky.wanted = dict(wanted)

    def __init__(self, table: QTableWidget):
        super().__init__(table)
        self._table = table
        self._applying = False
        self.wanted: dict[int, int] = {}
        table.installEventFilter(self)
        table.horizontalHeader().installEventFilter(self)  # the reset comes with the header's re-style
        table.horizontalHeader().sectionResized.connect(self._remember)

    def _remember(self, column: int, _old: int, new: int):
        # Only the user's own dragging counts: Qt's reset arrives column by column.
        if self._applying or new <= 0 or not _user_dragging():
            return
        self.wanted[column] = new

    def _is_reset(self) -> bool:
        """The columns we size are all back at the header default (the stretched last one aside)."""

        table = self._table
        header = table.horizontalHeader()
        default = header.defaultSectionSize()
        visible = [c for c in range(table.columnCount()) if not table.isColumnHidden(c)]
        last = max(visible, key=header.visualIndex) if visible else -1
        sized = [c for c in self.wanted if c in visible and c != last and header.sectionResizeMode(c) != QHeaderView.ResizeMode.Stretch]
        return bool(sized) and all(table.columnWidth(c) == default for c in sized) and any(
            self.wanted[c] != default for c in sized
        )

    def eventFilter(self, watched, event):  # noqa: N802 - Qt API name
        if event.type() in self._EVENTS:
            QTimer.singleShot(0, self.reapply)
        return super().eventFilter(watched, event)

    def reapply(self):
        try:
            if not self._is_reset():
                return
        except RuntimeError:  # the table is already gone
            return
        self._applying = True
        try:
            for column, width in self.wanted.items():
                if not self._table.isColumnHidden(column):
                    self._table.setColumnWidth(column, width)
        finally:
            self._applying = False


def fit_table_last_column(table: QTableWidget):
    """Stretch the final visible column to the table's right edge.

    The table pages let users hide and reorder fields. Applying this after
    every layout/resize keeps the visible table flush with its container instead
    of leaving a large blank strip when the saved widths are narrower than the
    current window.
    """
    if table is None or table.columnCount() <= 0:
        return
    header = table.horizontalHeader()
    visible_columns = [
        column
        for column in range(table.columnCount())
        if not table.isColumnHidden(column)
    ]
    if not visible_columns:
        return
    visible_columns.sort(key=header.visualIndex)
    last_column = visible_columns[-1]
    for column in visible_columns:
        header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
    header.setSectionResizeMode(last_column, QHeaderView.ResizeMode.Stretch)


def connect_table_width_saver(table: QTableWidget, key: str):
    saver = _TableWidthSaver(table, key)
    savers = getattr(table, "_ui_state_savers", [])
    savers.append(saver)
    table._ui_state_savers = savers


def restore_table_columns(
    table: QTableWidget,
    key: str,
    *,
    default_visible: list[int] | None = None,
    default_order: list[int] | None = None,
):
    header = table.horizontalHeader()
    header.setSectionsMovable(True)
    columns = _all_columns(table)
    if default_order is None:
        default_order = columns
    if default_visible is None:
        default_visible = columns
    layout = _load_column_layout(key, columns, default_order, default_visible)
    apply_table_column_layout(
        table,
        key,
        order=layout["order"],
        visible=layout["visible"],
        sync=False,
        persist=False,
    )


def connect_table_column_saver(table: QTableWidget, key: str):
    saver = _TableColumnSaver(table, key)
    savers = getattr(table, "_ui_state_savers", [])
    savers.append(saver)
    table._ui_state_savers = savers


def apply_table_column_layout(
    table: QTableWidget,
    key: str,
    *,
    order: list[int],
    visible: list[int],
    sync: bool,
    persist: bool = True,
):
    columns = _all_columns(table)
    valid_order = _normalize_column_order(order, columns)
    visible_set = {col for col in visible if col in columns}
    if not visible_set and columns:
        visible_set = {columns[0]}

    header = table.horizontalHeader()
    table.blockSignals(True)
    header.blockSignals(True)
    try:
        for target_visual, logical in enumerate(valid_order):
            current_visual = header.visualIndex(logical)
            if current_visual >= 0 and current_visual != target_visual:
                header.moveSection(current_visual, target_visual)
        for logical in columns:
            table.setColumnHidden(logical, logical not in visible_set)
    finally:
        header.blockSignals(False)
        table.blockSignals(False)
    if persist:
        _save_table_column_layout(table, key, sync=sync)


class _TableWidthSaver(QObject):
    def __init__(self, table: QTableWidget, key: str):
        super().__init__(table)
        self._table = table
        self._key = key
        self._sync_pending = False
        table.installEventFilter(self)
        header = table.horizontalHeader()
        # A drag by the user; Qt's own resets (re-style, reparent) must not be saved.
        header.sectionResized.connect(lambda *_args: _user_dragging() and self.save(sync=False))
        header.sectionMoved.connect(lambda *_args: self.save(sync=False))
        app = QApplication.instance()
        if app is not None:
            # A bound slot (not a lambda), so the connection goes away with this
            # saver when its table is destroyed (e.g. by a language rebuild).
            app.aboutToQuit.connect(self._save_on_quit)

    def _save_on_quit(self):
        self.save(sync=True)

    def eventFilter(self, watched, event):  # noqa: N802 - Qt API name
        if watched is self._table and event.type() in (
            QEvent.Type.Close,
            QEvent.Type.Hide,
            QEvent.Type.Destroy,
        ):
            self.save(sync=True)
        return super().eventFilter(watched, event)

    def save(self, *, sync: bool):
        try:
            if self._table.columnCount() <= 0:
                return
        except RuntimeError:  # the table is already gone
            return
        columns = [self._table.columnWidth(col) for col in range(self._table.columnCount())]
        if _looks_reset(self._table, columns):
            return  # Qt reset them (re-style / reparent); the real widths come back shortly
        app_config.set_ui_value(self._key, ",".join(str(width) for width in columns), sync=sync)
        if sync:
            self._sync_pending = False
            return
        self._schedule_sync()

    def _schedule_sync(self):
        if self._sync_pending:
            return
        self._sync_pending = True

        def flush():
            self._sync_pending = False
            app_config.sync()

        QTimer.singleShot(250, flush)


class _TableColumnSaver(QObject):
    def __init__(self, table: QTableWidget, key: str):
        super().__init__(table)
        self._table = table
        self._key = key
        self._sync_pending = False
        table.installEventFilter(self)
        header = table.horizontalHeader()
        header.sectionMoved.connect(lambda *_args: self.save(sync=False))

    def eventFilter(self, watched, event):  # noqa: N802 - Qt API name
        if watched is self._table and event.type() in (
            QEvent.Type.Close,
            QEvent.Type.Hide,
            QEvent.Type.Destroy,
        ):
            self.save(sync=True)
        return super().eventFilter(watched, event)

    def save(self, *, sync: bool):
        _save_table_column_layout(self._table, self._key, sync=sync)
        if sync:
            self._sync_pending = False
            return
        self._schedule_sync()

    def _schedule_sync(self):
        if self._sync_pending:
            return
        self._sync_pending = True

        def flush():
            self._sync_pending = False
            app_config.sync()

        QTimer.singleShot(500, flush)


def _save_table_column_layout(table: QTableWidget, key: str, *, sync: bool):
    payload = {
        "order": _current_column_order(table),
        "visible": [
            col for col in _all_columns(table)
            if not table.isColumnHidden(col)
        ],
    }
    app_config.set_ui_value(f"{key}_columns", json.dumps(payload), sync=sync)


def _load_column_layout(
    key: str,
    columns: list[int],
    default_order: list[int],
    default_visible: list[int],
) -> dict[str, list[int]]:
    raw = str(app_config.get_ui_value(f"{key}_columns", "") or "")
    if raw:
        try:
            payload = json.loads(raw)
            order = _normalize_column_order(payload.get("order", []), columns)
            visible = [int(col) for col in payload.get("visible", []) if int(col) in columns]
            if visible:
                return {"order": order, "visible": visible}
        except Exception:
            pass
    return {
        "order": _normalize_column_order(default_order, columns),
        "visible": [col for col in default_visible if col in columns],
    }


def _current_column_order(table: QTableWidget) -> list[int]:
    header = table.horizontalHeader()
    pairs = []
    for logical in _all_columns(table):
        visual = header.visualIndex(logical)
        pairs.append((visual if visual >= 0 else logical, logical))
    return [logical for _, logical in sorted(pairs)]


def _normalize_column_order(order: list[int], columns: list[int]) -> list[int]:
    normalized: list[int] = []
    for raw in order:
        try:
            col = int(raw)
        except Exception:
            continue
        if col in columns and col not in normalized:
            normalized.append(col)
    normalized.extend(col for col in columns if col not in normalized)
    return normalized


def _column_labels(table: QTableWidget) -> dict[int, str]:
    labels: dict[int, str] = {}
    for col in _all_columns(table):
        item = table.horizontalHeaderItem(col)
        text = item.text().strip() if item else ""
        labels[col] = text or str(col)
    return labels


def _all_columns(table: QTableWidget) -> list[int]:
    return list(range(table.columnCount()))


def _parse_int_list(raw: str) -> list[int]:
    values: list[int] = []
    for part in str(raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            values.append(int(part))
        except ValueError:
            return []
    return values
