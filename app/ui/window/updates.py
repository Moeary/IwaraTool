"""Offer new GitHub releases; the user downloads them from the release page."""

from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices

from ...presentation.updates import release_offer
from ..common.fluent_dialogs import show_fluent_confirmation


class WindowUpdatesMixin:
    """Offer new GitHub releases; the user downloads them from the release page."""

    def _on_release_update_available(self, release: dict):
        offer = release_offer(release)
        if offer is None:
            return
        self._show_desktop_notification(*offer.notification)
        title, message, informative, yes_text, no_text = offer.question
        if show_fluent_confirmation(self, title, message, informative=informative, yes_text=yes_text, no_text=no_text):
            QDesktopServices.openUrl(QUrl(offer.url))
