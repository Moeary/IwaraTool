"""Export download history records as CSV."""

from __future__ import annotations

import csv
import os
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog

from qfluentwidgets import InfoBar, InfoBarPosition

from ...config import app_config
from ...i18n import tr
from .record_state import _video_url


HISTORY_CSV_FIELDS = (
    "video_id",
    "title",
    "author",
    "quality",
    "published_at",
    "likes",
    "views",
    "downloaded_at",
    "file_path",
    "source_url",
)


def write_history_csv(path: str, records: list[dict[str, Any]]):
    """Write history rows as UTF-8 CSV with a BOM so Excel opens CJK text."""
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(HISTORY_CSV_FIELDS)
        for record in records:
            row = [str(record.get(field, "") or "") for field in HISTORY_CSV_FIELDS]
            if not row[-1]:
                row[-1] = _video_url(row[0])
            writer.writerow(row)


class HistoryCsvExportMixin:
    """Export download history records as CSV."""

    def _export_visible_csv(self):
        records = list(getattr(self, "_visible_records", []) or [])
        if not records:
            InfoBar.warning(
                title=tr("Nothing to export", "没有可导出的记录", "書き出す履歴がありません"),
                content="",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=2000,
                parent=self,
            )
            return
        default_path = os.path.join(app_config.app_data_dir, "history_export.csv")
        path, _selected = QFileDialog.getSaveFileName(
            self,
            tr("Export history", "导出历史记录", "履歴を書き出す"),
            default_path,
            "CSV (*.csv)",
        )
        if not path:
            return
        try:
            write_history_csv(path, records)
        except OSError as exc:
            InfoBar.error(
                title=tr("Export failed", "导出失败", "書き出しに失敗"),
                content=str(exc),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )
            return
        InfoBar.success(
            title=tr("Exported", "已导出", "書き出しました"),
            content=tr(
                f"{len(records)} records → {path}",
                f"{len(records)} 条记录 → {path}",
                f"{len(records)} 件 → {path}",
            ),
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=3000,
            parent=self,
        )
