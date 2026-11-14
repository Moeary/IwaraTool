"""Back history, one stack per page.

Every place the user can be is a *page* (a sidebar page in the main window, or
a lone page widget in tests) plus that page's own snapshot: a plain-data
description of what it shows (which post, which search session, which
subscription, the page of results, the scroll position, the selection...).

Before anything navigates, the current place is recorded on the stack of the
page being navigated to; Back pops the stack of the page in front and restores
that record exactly, from the snapshot alone, without asking the network
again.  So each page goes back through its own steps: opening a post on Home,
visiting Search from the sidebar and returning, Back on Home leads back to the
Home feed, not to Search.  A link that jumps to another page (an author page
from a search result) is recorded on the target page, so Back there returns
to the caller.  Plain page switches (sidebar, Ctrl+1…) are not steps.

Pages opt in with two methods::

    def nav_snapshot(self) -> object: ...
    def nav_restore(self, state: object) -> None: ...

Pages without them are still recorded (Back shows them again as they are).
Snapshots must not hold widgets: they outlive their window when the window is
rebuilt for a language change.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterator

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QAbstractScrollArea, QScrollBar, QWidget


HISTORY_LIMIT = 50


@dataclass
class NavEntry:
    page: QWidget
    state: Any


def snapshot_of(page: QWidget) -> Any:
    snapshot = getattr(page, "nav_snapshot", None)
    if not callable(snapshot):
        return None
    try:
        return snapshot()
    except RuntimeError:  # a widget of the page was already destroyed
        return None


def restore_into(page: QWidget, state: Any) -> None:
    restore = getattr(page, "nav_restore", None)
    if callable(restore) and state is not None:
        restore(state)


def draft_of(page: QWidget) -> Any:
    """Unsaved input a page holds (typed links, an unsaved form), as plain data.

    Unlike ``nav_snapshot`` this is not a place in the history; it is only
    carried into the new window when the window is rebuilt for a language change.
    """

    draft = getattr(page, "draft_state", None)
    if not callable(draft):
        return None
    try:
        return draft()
    except RuntimeError:
        return None


def restore_draft_into(page: QWidget, state: Any) -> None:
    restore = getattr(page, "restore_draft", None)
    if callable(restore) and state is not None:
        restore(state)


def _same_place(first: NavEntry, second: NavEntry) -> bool:
    if first.page is not second.page:
        return False
    try:
        return bool(first.state == second.state)
    except Exception:
        return False


class NavigationController:
    """The back stacks of one window, one per page.

    ``resolve_page`` maps any widget to the page that owns it; ``switch_to``
    brings a page to the front (the main window's sidebar switch) and
    ``current_page`` tells which page is in front now.
    """

    def __init__(
        self,
        *,
        resolve_page: Callable[[QWidget], QWidget | None],
        current_page: Callable[[], QWidget | None],
        switch_to: Callable[[QWidget], None] | None = None,
    ):
        self._resolve_page = resolve_page
        self._current_page = current_page
        self._switch_to = switch_to
        self._stacks: dict[QWidget, list[NavEntry]] = {}
        self._quiet = 0

    # ── state ────────────────────────────────────────────────────────────────

    def _stack(self, page: QWidget | None) -> list[NavEntry]:
        if page is None:
            return []
        return self._stacks.get(page, [])

    def __len__(self) -> int:
        """Steps Back can take on the page in front."""

        return len(self._stack(self._current_page()))

    def can_go_back(self) -> bool:
        return bool(self._stack(self._current_page()))

    def entries(self, page: QWidget | None = None) -> list[NavEntry]:
        """The back stack of ``page`` (default: the page in front), oldest first."""

        return list(self._stack(page if page is not None else self._current_page()))

    def stacks(self) -> dict[QWidget, list[NavEntry]]:
        return {page: list(entries) for page, entries in self._stacks.items() if entries}

    def replace(self, stacks: dict[QWidget, list[NavEntry]]) -> None:
        self._stacks = {page: list(entries)[-HISTORY_LIMIT:] for page, entries in stacks.items() if entries}

    def clear(self) -> None:
        self._stacks.clear()

    @contextmanager
    def quiet(self) -> Iterator[None]:
        """Moves made inside are not recorded (they are a restore, or already recorded)."""

        self._quiet += 1
        try:
            yield
        finally:
            self._quiet -= 1

    @property
    def is_quiet(self) -> bool:
        return self._quiet > 0

    # ── moves ────────────────────────────────────────────────────────────────

    def record(self, widget: QWidget | None = None, *, into: QWidget | None = None) -> bool:
        """Remember where the user is, before they go somewhere else.

        The record goes on the stack of ``into``, the page about to be shown
        (default: the page being left, for a move inside one page).
        """

        if self._quiet:
            return False
        page = self._resolve_page(widget) if widget is not None else self._current_page()
        if page is None:
            return False
        target = into if into is not None else page
        entry = NavEntry(page, snapshot_of(page))
        stack = self._stacks.setdefault(target, [])
        if stack and _same_place(stack[-1], entry):
            return False
        stack.append(entry)
        del stack[:-HISTORY_LIMIT]
        return True

    def back(self) -> bool:
        """Return to the last place recorded for the page in front; False (and nothing changes) without one."""

        current = self._current_page()
        stack = self._stacks.get(current) if current is not None else None
        while stack:
            entry = stack.pop()
            try:
                entry.page.objectName()
            except RuntimeError:  # its page is gone
                continue
            with self.quiet():
                if self._switch_to is not None and self._current_page() is not entry.page:
                    self._switch_to(entry.page)
                restore_into(entry.page, entry.state)
            return True
        return False


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
