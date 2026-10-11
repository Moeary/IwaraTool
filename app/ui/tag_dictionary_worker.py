"""Background worker used to refresh the localized search-tag dictionary."""
from __future__ import annotations

from PySide6.QtCore import Signal

from ..core.manager import download_manager
from .worker_lifecycle import ManagedThread


class TagDictionaryUpdateWorker(ManagedThread):
    """Refresh the tag dictionary without blocking the settings page."""

    result_ready = Signal(object)

    def run(self):
        try:
            self.result_ready.emit(download_manager.update_search_tag_dictionary())
        except Exception as exc:
            self.result_ready.emit((0, str(exc)))
