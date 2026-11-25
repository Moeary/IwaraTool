"""Back history for the PySide6 window (the stacks themselves are ``presentation.navigation``).

Snapshots must not hold widgets: they outlive their window when the window is
rebuilt for a language change.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QAbstractScrollArea, QScrollBar, QWidget

from ...presentation import navigation as _navigation
from ...presentation.navigation import (  # noqa: F401 - re-exported
    HISTORY_LIMIT,
    NavEntry,
    _same_place,
    draft_of,
    restore_draft_into,
    restore_into,
    snapshot_of,
)


def _widget_alive(page: QWidget) -> bool:
    try:
        page.objectName()
    except RuntimeError:  # the C++ widget was destroyed
        return False
    return True


class NavigationController(_navigation.NavigationController):
    """The back stacks of one window; recorded pages that were destroyed are skipped."""

    def __init__(self, *, resolve_page, current_page, switch_to=None):
        super().__init__(
            resolve_page=resolve_page, current_page=current_page, switch_to=switch_to, is_alive=_widget_alive,
        )


def _outermost_page(widget: QWidget) -> QWidget | None:
    page = None
    current: QWidget | None = widget
    while current is not None:
        if callable(getattr(current, "nav_snapshot", None)):
            page = current
        current = current.parentWidget()
    return page


def controller_for(widget: QWidget) -> NavigationController:
    """The window's controller, or one kept on a lone page (tests, tools)."""

    window = widget.window()
    controller = getattr(window, "navigation", None)
    if isinstance(controller, NavigationController):
        return controller
    controller = getattr(window, "_fallback_navigation", None)
    if not isinstance(controller, NavigationController):
        controller = NavigationController(
            resolve_page=_outermost_page,
            current_page=lambda window=window: _outermost_page(window),
        )
        window._fallback_navigation = controller
    return controller


def record_navigation(widget: QWidget) -> bool:
    """Call right before ``widget``'s page shows something else."""

    return controller_for(widget).record(widget)


def navigate_back(widget: QWidget) -> bool:
    return controller_for(widget).back()


@contextmanager
def quiet_navigation(widget: QWidget) -> Iterator[None]:
    with controller_for(widget).quiet():
        yield


# ── restoring view positions ────────────────────────────────────────────────


def scroll_bar_of(area: QAbstractScrollArea | QScrollBar) -> QScrollBar:
    return area if isinstance(area, QScrollBar) else area.verticalScrollBar()


def restore_scroll(area: QAbstractScrollArea | QScrollBar, value: int) -> None:
    """Put the scroll position back, also once the rebuilt content is laid out."""

    bar = scroll_bar_of(area)
    value = max(0, int(value or 0))
    bar.setValue(value)
    if value:
        for delay in (0, 60):
            QTimer.singleShot(delay, bar, lambda bar=bar, value=value: bar.setValue(value))


def has_focus_within(widget: QWidget) -> bool:
    from PySide6.QtWidgets import QApplication

    focus = QApplication.focusWidget()
    return focus is not None and (focus is widget or widget.isAncestorOf(focus))
