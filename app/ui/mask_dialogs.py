"""qfluentwidgets mask dialogs that still take clicks once the window has native views.

A qfluentwidgets ``MaskDialogBase`` is not a window: it is a child widget that
covers its parent with a translucent mask.  Qt's ``QVideoWidget`` embeds a
native window, and inside a scroll area (the post page) that makes the main
window's widgets native as well, including a mask dialog shown afterwards.  A
native child shown through ``exec()`` is application modal, and Qt then
blocks its own input: it checks ``dialog.window()``, the main window, against
the modal dialog and drops the event.  The dialog stays on screen and nothing
can be clicked (macOS, after playing a video on a post page).

The mask already covers what the dialog guards, so in that case the dialog is
shown without Qt modality and a local event loop waits for it instead.
"""

from __future__ import annotations

from PySide6.QtCore import QEventLoop
from PySide6.QtWidgets import QWidget

from qfluentwidgets.components.dialog_box.mask_dialog_base import MaskDialogBase

_original_exec = MaskDialogBase.exec


def _among_native_siblings(dialog: QWidget) -> bool:
    """Whether the dialog will be a native view (its siblings already are)."""

    if dialog.isWindow():
        return False
    if dialog.internalWinId():
        return True
    parent = dialog.parentWidget()
    if parent is None:
        return False
    return any(
        isinstance(child, QWidget) and child is not dialog and not child.isWindow() and child.internalWinId()
        for child in parent.children()
    )


def _exec(self: MaskDialogBase) -> int:
    if not _among_native_siblings(self):
        return _original_exec(self)
    loop = QEventLoop()
    self.finished.connect(loop.quit)
    self.destroyed.connect(loop.quit)
    self.setResult(0)
    self.show()
    self.raise_()
    loop.exec()
    try:
        return self.result()
    except RuntimeError:  # deleted while open
        return 0


def install() -> None:
    """Route every mask dialog's ``exec()`` through the native-safe version."""

    MaskDialogBase.exec = _exec


def open_mask_dialog(window: QWidget) -> MaskDialogBase | None:
    """The mask dialog currently shown over ``window``, if any."""

    for dialog in reversed(window.findChildren(MaskDialogBase)):
        if dialog.isVisible():
            return dialog
    return None
