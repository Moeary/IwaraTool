"""Background lookup behind the workbench's "why not downloaded"."""

from __future__ import annotations

from PySide6.QtCore import Signal

from ..worker_lifecycle import ManagedThread
from .page_dependencies import download_manager


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
