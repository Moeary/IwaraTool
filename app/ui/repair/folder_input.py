"""Folder path box that accepts a dropped folder."""

from __future__ import annotations

import os

from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QWidget

from qfluentwidgets import LineEdit


class FolderDropLineEdit(LineEdit):
    """Line edit that accepts a dropped local directory."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAcceptDrops(True)

    @staticmethod
    def _directory_from_event(event: QDragEnterEvent | QDropEvent) -> str:
        mime = event.mimeData()
        if not mime.hasUrls():
            return ""
        for url in mime.urls():
            if url.isLocalFile():
                path = url.toLocalFile()
                if os.path.isdir(path):
                    return path
        return ""

    def dragEnterEvent(self, event: QDragEnterEvent):
        if self._directory_from_event(event):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dropEvent(self, event: QDropEvent):
        directory = self._directory_from_event(event)
        if directory:
            self.setText(directory)
            event.acceptProposedAction()
            return
        super().dropEvent(event)
