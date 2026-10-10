"""Background lookups behind the workbench's link preview and "why not downloaded"."""

from __future__ import annotations

from PySide6.QtCore import Signal

from ..worker_lifecycle import ManagedThread
from .page_dependencies import download_manager


class ListingPreviewWorker(ManagedThread):
    """Fetch every video behind an author / playlist link and classify it."""

    result_ready = Signal(dict)

    def __init__(self, url: str, rule_id: str):
        super().__init__()
        self._url = url
        self._rule_id = rule_id

    def run(self):
        try:
            result = download_manager.preview_listing(self._url, rule_id=self._rule_id)
        except Exception as exc:  # a network or parsing error must still close the busy state
            result = {"url": self._url, "error": str(exc), "items": []}
        if not self.isInterruptionRequested():
            self.result_ready.emit(result)


class DiagnosisWorker(ManagedThread):
    """Collect the reasons one video was not downloaded."""

    result_ready = Signal(dict)

    def __init__(self, text: str, rule_id: str):
        super().__init__()
        self._text = text
        self._rule_id = rule_id

    def run(self):
        try:
            result = download_manager.explain_not_downloaded(self._text, rule_id=self._rule_id)
        except Exception as exc:
            result = {
                "video_id": "",
                "title": "",
                "verdict": str(exc),
                "findings": [{"level": "block", "text": str(exc)}],
                "can_download": False,
            }
        if not self.isInterruptionRequested():
            self.result_ready.emit(result)
