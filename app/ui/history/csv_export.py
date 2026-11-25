"""Export download history records as CSV."""

from __future__ import annotations

import os
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog

from qfluentwidgets import InfoBar, InfoBarPosition

from ...config import app_config
from ...presentation.history import library
from ...presentation.history.library import HISTORY_CSV_FIELDS, write_history_csv  # noqa: F401  (re-exported)


class HistoryCsvExportMixin:
    """Export download history records as CSV."""

    def _export_visible_csv(self):
        records = list(getattr(self, "_visible_records", []) or [])
        if not records:
            InfoBar.warning(
                title=library.nothing_to_export_text(),
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
            library.export_title(),
            default_path,
            "CSV (*.csv)",
        )
        if not path:
            return
        try:
            write_history_csv(path, records)
        except OSError as exc:
            InfoBar.error(
                title=library.export_failed_title(),
                content=str(exc),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )
            return
        title, content = library.exported_notice(len(records), path)
        InfoBar.success(
            title=title,
            content=content,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=3000,
            parent=self,
        )
