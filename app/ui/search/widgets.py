"""Keyword box and its tag-suggestion and history popups."""
from __future__ import annotations

import json
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QAbstractItemView, QListWidgetItem, QWidget

from qfluentwidgets import LineEdit, ListWidget

from ...core.tag_dictionary import TagSuggestion
from ...i18n import tr
from ..theme import popup_list_qss


class TagSuggestionPopup(ListWidget):
    """Small Fluent-compatible popup for localized tag candidates."""

    suggestion_chosen = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        # ``Qt.Popup`` grabs the application's keyboard even with
        # ``NoFocus``.  That makes the first typed character open the list and
        # prevents the user from continuing to type a tag.  A non-activating
        # tool window keeps mouse selection while leaving the LineEdit active.
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setMinimumWidth(360)
        self.setMaximumHeight(260)
        self.setStyleSheet(popup_list_qss())
        self.itemClicked.connect(self._choose_item)

    def showEvent(self, event):
        # Popups outlive theme switches; recolor them whenever they open.
        self.setStyleSheet(popup_list_qss())
        super().showEvent(event)

    def set_suggestions(self, suggestions: list[TagSuggestion]):
        self.clear()
        for suggestion in suggestions:
            item = QListWidgetItem(suggestion.display_text)
            item.setData(Qt.ItemDataRole.UserRole, suggestion.key)
            item.setToolTip(
                f"{suggestion.key}\n"
                f"EN: {suggestion.en}\n"
                f"中文: {suggestion.zh}\n"
                f"日本語: {suggestion.ja}"
            )
            self.addItem(item)
        if self.count():
            self.setCurrentRow(0)

    def _choose_item(self, item: QListWidgetItem):
        value = item.data(Qt.ItemDataRole.UserRole)
        if value:
            self.suggestion_chosen.emit(str(value))


class SearchKeywordEdit(LineEdit):
    """Line edit that lets the page anchor an on-demand history popup."""

    activated = Signal()
    deactivated = Signal()

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.activated.emit()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.deactivated.emit()

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        self.activated.emit()


class SearchHistoryPopup(ListWidget):
    """Non-activating dropdown anchored to the search keyword field."""

    history_chosen = Signal(str)
    clear_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setMinimumWidth(360)
        self.setMaximumHeight(280)
        self.setStyleSheet(popup_list_qss())
        self.itemClicked.connect(self._choose_item)

    def showEvent(self, event):
        self.setStyleSheet(popup_list_qss())
        super().showEvent(event)

    def set_history(self, entries: list[dict[str, str]], formatter):
        self.clear()
        for entry in entries:
            item = QListWidgetItem(formatter(entry))
            item.setData(
                Qt.ItemDataRole.UserRole,
                json.dumps(entry, ensure_ascii=False, separators=(",", ":")),
            )
            self.addItem(item)
        if entries:
            clear_item = QListWidgetItem(
                tr("Clear search history", "清空搜索历史", "検索履歴を消去")
            )
            clear_item.setData(Qt.ItemDataRole.UserRole, "__clear__")
            self.addItem(clear_item)
            self.setCurrentRow(0)

    def _choose_item(self, item: QListWidgetItem):
        value = str(item.data(Qt.ItemDataRole.UserRole) or "")
        self.hide()
        if value == "__clear__":
            self.clear_requested.emit()
        elif value:
            self.history_chosen.emit(value)
