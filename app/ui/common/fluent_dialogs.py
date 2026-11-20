"""Fluent-styled confirmation and text-input dialogs."""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDialog

from qfluentwidgets import BodyLabel, LineEdit, MessageBox, MessageBoxBase, SubtitleLabel


def show_fluent_confirmation(
    parent,
    title: str,
    content: str,
    *,
    informative: str = "",
    yes_text: str | None = None,
    no_text: str | None = None,
) -> bool:
    """Show a theme-aware Fluent confirmation dialog and return whether accepted."""
    message = content
    if informative:
        message = f"{content}\n\n{informative}"
    box = MessageBox(title, message, parent)
    if yes_text:
        box.yesButton.setText(yes_text)
    if no_text:
        box.cancelButton.setText(no_text)
    return box.exec() == QDialog.DialogCode.Accepted


class _FluentTextInputDialog(MessageBoxBase):
    """Small reusable Fluent text-entry dialog."""

    def __init__(self, parent, title: str, prompt: str, text: str = ""):
        super().__init__(parent)
        self.title_label = SubtitleLabel(title, self)
        self.prompt_label = BodyLabel(prompt, self)
        self.prompt_label.setWordWrap(True)
        self.line_edit = LineEdit(self)
        self.line_edit.setText(text)
        self.line_edit.returnPressed.connect(self.yesButton.click)

        self.viewLayout.addWidget(self.title_label)
        self.viewLayout.addWidget(self.prompt_label)
        self.viewLayout.addWidget(self.line_edit)
        self.widget.setMinimumWidth(520)
        QTimer.singleShot(0, self._focus_input)

    def _focus_input(self):
        self.line_edit.setFocus()
        self.line_edit.selectAll()


def show_fluent_text_input(
    parent,
    title: str,
    prompt: str,
    *,
    text: str = "",
    accept_text: str | None = None,
    cancel_text: str | None = None,
) -> tuple[str, bool]:
    """Show a theme-aware Fluent text input and return ``(text, accepted)``."""
    box = _FluentTextInputDialog(parent, title, prompt, text)
    if accept_text:
        box.yesButton.setText(accept_text)
    if cancel_text:
        box.cancelButton.setText(cancel_text)
    accepted = box.exec() == QDialog.DialogCode.Accepted
    return box.line_edit.text(), accepted
