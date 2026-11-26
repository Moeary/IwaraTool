"""Where the Flet shell is, and how Back gets out of it (docs/flet_ui_plan.md 4.4).

No Flet import.  Every shell destination has a ``Destination`` handle that
remembers the route it shows (detail and author routes open *over* the
destination they were reached from) plus, optionally, the state its page
store hands over.  The back stacks are ``presentation.navigation``'s, the same
rules as the PySide6 window: switching destinations is not a step, each
destination goes back through its own steps, and a link into another
destination is recorded there so Back returns to the caller.
"""
from __future__ import annotations

from typing import Any, Callable

from ..presentation.navigation import NavigationController
from . import routes


class Destination:
    """One shell destination; its snapshot is its route plus its page's state."""

    def __init__(self, name: str):
        self.name = name
        self.path = routes.route(name)
        self.snapshot_state: Callable[[], Any] | None = None
        self.restore_state: Callable[[Any], None] | None = None

    def nav_snapshot(self) -> tuple[str, Any]:
        return self.path, self.snapshot_state() if self.snapshot_state else None

    def nav_restore(self, state: tuple[str, Any]) -> None:
        path, page_state = state
        self.path = path
        if self.restore_state is not None and page_state is not None:
            self.restore_state(page_state)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Destination({self.path})"


class ShellNavigator:
    """Destination switching, route opening and Back for one window.

    ``on_change(destination, path)`` runs after every move so the store (and
    so the interface) follows.
    """

    def __init__(self, on_change: Callable[[str, str], None], *, start: str = routes.HOME):
        self._on_change = on_change
        self.destinations = {name: Destination(name) for name in routes.DESTINATIONS}
        self.current = self.destinations.get(start, self.destinations[routes.HOME])
        self.history = NavigationController(
            resolve_page=lambda page: page,
            current_page=lambda: self.current,
            switch_to=self._show,
        )

    # ── state ────────────────────────────────────────────────────────────────

    @property
    def path(self) -> str:
        return self.current.path

    def can_go_back(self) -> bool:
        return self.history.can_go_back() or not routes.parse(self.current.path).is_root

    def _show(self, destination: Destination) -> None:
        self.current = destination

    def _changed(self) -> None:
        self._on_change(self.current.name, self.current.path)

    # ── moves ────────────────────────────────────────────────────────────────

    def switch(self, name: str) -> None:
        """Rail / bottom bar / Ctrl+1…: show a destination as the user left it."""

        target = self.destinations.get(name)
        if target is None or target is self.current:
            return
        self.current = target
        self._changed()

    def go_root(self, name: str) -> None:
        """Re-selecting the current destination returns it to its top (not a step)."""

        target = self.destinations.get(name)
        if target is None:
            return
        self.current = target
        self.current.path = routes.route(name)
        self._changed()

    def open(self, path: str) -> None:
        """Follow a link; the place left behind is a step Back returns to."""

        route = routes.parse(path, fallback=self.current.name)
        if route.destination in (routes.DETAIL, routes.AUTHOR):
            target = self.current  # opens over the destination it came from
        else:
            target = self.destinations[route.destination]
        if route.path == target.path and target is self.current:
            return
        self.history.record(self.current, into=target)
        self.current = target
        target.path = route.path
        self._changed()

    def show(self, path: str) -> None:
        """Change the current destination's sub-route without a Back step (a list beside its detail)."""

        route = routes.parse(path, fallback=self.current.name)
        target = self.destinations.get(route.destination)
        if target is None or target is not self.current or route.path == target.path:
            return
        target.path = route.path
        self._changed()

    def back(self) -> bool:
        if self.history.back():
            self._changed()
            return True
        route = routes.parse(self.current.path)
        if not route.is_root:  # reached a sub-route without steps (a deep link)
            self.current.path = routes.route(self.current.name)
            self._changed()
            return True
        return False
