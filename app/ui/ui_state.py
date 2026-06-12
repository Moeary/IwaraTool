"""Small helpers for persistent UI geometry."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtWidgets import QSplitter, QTableWidget

from ..config import app_config


def restore_splitter_sizes(splitter: QSplitter, key: str, default_sizes: list[int]):
    raw = str(app_config.get_ui_value(key, "") or "")
    sizes = _parse_int_list(raw)
    splitter.setSizes(sizes if len(sizes) == len(default_sizes) else default_sizes)


def connect_splitter_saver(splitter: QSplitter, key: str):
    splitter.splitterMoved.connect(lambda *_args: app_config.set_ui_value(key, ",".join(str(v) for v in splitter.sizes())))


def restore_table_widths(table: QTableWidget, key: str, default_widths: dict[int, int]):
    raw = str(app_config.get_ui_value(key, "") or "")
    widths = _parse_int_list(raw)
    for col, default in default_widths.items():
        width = widths[col] if 0 <= col < len(widths) and widths[col] > 0 else default
        table.setColumnWidth(col, width)


def connect_table_width_saver(table: QTableWidget, key: str):
    saver = _TableWidthSaver(table, key)
    savers = getattr(table, "_ui_state_savers", [])
    savers.append(saver)
    table._ui_state_savers = savers


class _TableWidthSaver(QObject):
    def __init__(self, table: QTableWidget, key: str):
        super().__init__(table)
        self._table = table
        self._key = key
        self._sync_pending = False
        table.installEventFilter(self)
        header = table.horizontalHeader()
        header.sectionResized.connect(lambda *_args: self.save(sync=False))
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
        if self._table.columnCount() <= 0:
            return
        widths = [str(self._table.columnWidth(col)) for col in range(self._table.columnCount())]
        app_config.set_ui_value(self._key, ",".join(widths), sync=sync)
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
