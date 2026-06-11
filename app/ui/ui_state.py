"""Small helpers for persistent UI geometry."""
from __future__ import annotations

from PySide6.QtCore import QTimer
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
    pending = {"active": False}

    def save_later():
        if pending["active"]:
            return
        pending["active"] = True

        def save():
            pending["active"] = False
            widths = [str(table.columnWidth(col)) for col in range(table.columnCount())]
            app_config.set_ui_value(key, ",".join(widths))

        QTimer.singleShot(250, save)

    table.horizontalHeader().sectionResized.connect(lambda *_args: save_later())


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
