"""The window's controller: state, navigation, global preferences and events.

One ``Shell`` per window, handed to components through ``ShellContext``.
Components call its methods; it writes ``AppState`` and the components that
read the changed fields re-render.  Blocking work (the core's settings and
download queries) runs with ``asyncio.to_thread``.
"""
from __future__ import annotations

from collections.abc import Callable  # noqa: F401 - annotations

import asyncio

import flet as ft

from ..config import app_config
from ..core import account
from ..core.download.manager import download_manager
from ..i18n import current_language
from ..logging_setup import get_logger
from ..presentation import clipboard_links, preferences
from ..presentation.media import card_size
from ..presentation.updates import release_offer
from ..presentation.downloads.task_statuses import badge_counts
from . import layout, routes
from .navigation import ShellNavigator
from .services.system_notices import SystemNotices
from .platform.capabilities import MOBILE_PLATFORMS, capabilities, platform_override
from .services.dialogs import confirm
from .services.downloads import DownloadService
from .services.events import EventBridge
from .services.images import ImageService
from .shortcuts import ShortcutMap, event_chord
from .stores.app_state import AppState
from .stores.detail import DetailStore
from .stores.library import LibraryStore
from .stores.repair import RepairStore
from .stores.rules import RulesStore
from .stores.tools import ToolsStore
from .stores.subscriptions import SubscriptionStore
from .stores.home import HomeStore
from .stores.search import SearchStore
from .stores.tasks import TaskStore
from .theme import apply_theme
from .tokens import Size

logger = get_logger(__name__)

RAIL_EXTENDED_KEY = "flet_rail_extended"
RESIZE_SETTLE = 0.016  # one frame: dragging the window edge does not rebuild on every pixel

_NAV_SHORTCUTS = {
    "nav_home": routes.HOME,
    "nav_subscriptions": routes.SUBSCRIPTIONS,
    "nav_search": routes.SEARCH,
    "nav_download": routes.DOWNLOADS,
    "nav_history": routes.LIBRARY,
    "nav_rules": routes.RULES,
    "nav_repair": routes.REPAIR,
    "nav_tools": routes.TOOLS,
    "nav_settings": routes.SETTINGS,
    "open_settings": routes.SETTINGS,
}


def platform_name(page: ft.Page) -> str:
    """The page's platform, or the one ``--platform`` asks a desktop run to behave as."""
    return platform_override() or str(page.platform.value if page.platform else "macos").lower()


class Shell:
    def __init__(self, page: ft.Page, *, start: str | None = None, theme_override: str = ""):
        self.page = page
        self.loop = asyncio.get_running_loop()
        self.theme_override = theme_override  # dev/screenshot runs: not saved
        desktop = platform_name(page) not in MOBILE_PLATFORMS
        first = start or routes.startup_destination(app_config.startup_page, desktop=desktop)
        if not routes.allowed(first, desktop=desktop):
            first = routes.HOME  # --route /repair on a phone
        self.state = AppState(
            layout=self._layout(page.width or 1280, page.height or 800),
            destination=first,
            path=routes.route(first),
            language=current_language(),
            theme_mode=theme_override or preferences.normalize_theme_mode(app_config.theme_mode),
            rating=preferences.current_rating(),
            site=preferences.current_site(),
            logged_in=account.is_logged_in(download_manager),
            cover_size=card_size.saved_card_width(),
        )
        self.navigator = ShellNavigator(self._on_navigated, start=first)
        self.bridge = EventBridge(self.loop)
        self.events = self.bridge.group()
        self.shortcuts = ShortcutMap()
        self.manager = download_manager
        # Settings records a new shortcut: the next key press goes here instead of running an action.
        self.key_capture: Callable[[str, str], None] | None = None
        self.downloads = DownloadService(self)
        self.tasks = TaskStore(download_manager)
        self.images = ImageService(download_manager)
        self.search = SearchStore(self, download_manager)
        self.home = HomeStore(self, download_manager)
        self.details = DetailStore(self, download_manager)
        self.subscriptions = SubscriptionStore(self, download_manager)
        self.library = LibraryStore(self, download_manager)
        self.rules = RulesStore(self, download_manager)
        self.repair = RepairStore(self, download_manager)
        self.tools = ToolsStore(self)
        self.notices = SystemNotices(self, download_manager)
        self.drafts: dict[str, str] = {}  # unsent input (typed links…) kept across destination switches
        self._resize_handle: asyncio.TimerHandle | None = None
        self._awake = False
        self._offered_clipboard = ""
        self._last_tool = "rules"  # the Tools entry reopens the tool used last (presentation/tools/hub.py keys)
        self._pending_size: tuple[float, float] | None = None

    # ── layout ───────────────────────────────────────────────────────────────

    def _layout(self, width: float, height: float) -> layout.LayoutState:
        extended = bool(app_config.get_ui_value(RAIL_EXTENDED_KEY, True))
        return layout.LayoutState(width, height, platform_name(self.page), rail_extended_preference=extended)

    def on_resize(self, event: ft.PageResizeEvent) -> None:
        self._pending_size = (event.width, event.height)
        if self._resize_handle is None:
            self._resize_handle = self.loop.call_later(RESIZE_SETTLE, self._apply_size)

    def _apply_size(self) -> None:
        self._resize_handle = None
        if self._pending_size is None:
            return
        width, height = self._pending_size
        self._pending_size = None
        current = self.state.layout
        if (current.width, current.height) != (width, height):
            self.state.layout = self._layout(width, height)
            self._sync_notice_frame()

    def _sync_notice_frame(self) -> None:
        from .services.notify import set_frame

        state = self.state.layout
        set_frame(self.page, width=state.width, bottom_inset=Size.NAV_BAR if state.nav_mode == layout.NAV_BAR else 0)

    def toggle_rail(self) -> None:
        extended = not self.state.layout.rail_extended_preference
        app_config.set_ui_value(RAIL_EXTENDED_KEY, extended)
        self.state.layout = self._layout(self.state.layout.width, self.state.layout.height)

    # ── navigation ───────────────────────────────────────────────────────────

    def _on_navigated(self, destination: str, path: str) -> None:
        self.state.destination = destination
        self.state.path = path
        self.state.can_back = self.navigator.can_go_back()
        self.state.account_open = False

    def select(self, destination: str) -> None:
        """A navigation item was clicked: switch, or return to the top when already there."""
        if not routes.allowed(destination, desktop=self.caps.desktop):
            return
        if destination == self.state.destination:
            self.navigator.go_root(destination)
        else:
            self.navigator.switch(destination)

    def select_entry(self, entry: str) -> None:
        """A rail / bar / panel entry: Tools opens the tool used last (rules at first)."""
        if entry == routes.TOOLS and self.caps.desktop:
            if not self.state.layout.two_pane:
                self.select(routes.TOOLS)  # the narrow window's tool list
            elif self.state.destination in routes.TOOL_GROUP:
                self.navigator.go_root(self.state.destination)
            else:
                self.open_tool(self._last_tool)
            return
        self.select(entry)

    def open_tool(self, key: str) -> None:
        """A Tools hub entry: rules, repair, or an upload tool (presentation/tools/hub.py)."""
        self._last_tool = key
        target = {"rules": routes.RULES, "repair": routes.REPAIR}.get(key, routes.TOOLS)
        if not routes.allowed(target, desktop=self.caps.desktop):
            return
        if target == routes.TOOLS:
            self.tools.choose_tool(key)
            if not self.state.layout.two_pane:
                self.open(routes.route(routes.TOOLS, key))
                return
        if target == self.state.destination:
            self.navigator.go_root(target)
        else:
            self.navigator.switch(target)

    def open(self, path: str) -> None:
        if not routes.allowed(path, desktop=self.caps.desktop):
            return  # a link to a desktop-only page (repair, tools) on a phone
        self.navigator.open(path)

    def back(self) -> bool:
        if self.state.account_open:
            self.state.account_open = False
            return True
        if self.navigator.back():
            return True
        if self.caps.mobile and self.state.destination != routes.HOME:
            # Material: back from another top-level destination goes home before it leaves the app.
            self.navigator.switch(routes.HOME)
            return True
        return False

    def cycle_destination(self, step: int) -> None:
        items = self.visible_destinations()
        if self.state.destination not in items:
            self.navigator.switch(items[0])
            return
        index = (items.index(self.state.destination) + step) % len(items)
        self.navigator.switch(items[index])

    def visible_destinations(self) -> tuple[str, ...]:
        return routes.available(routes.DESTINATIONS, desktop=self.state.layout.is_desktop)

    # ── global preferences (each also announced on signal_bus) ──────────────

    def set_rating(self, rating: str) -> None:
        self.state.rating = preferences.set_rating(rating)

    def cycle_rating(self) -> None:
        self.state.rating = preferences.cycle_rating()

    def set_site(self, key: str) -> None:
        preferences.set_site(key)
        self.state.site = preferences.current_site()

    def toggle_site(self) -> None:
        from ..core import site as sites

        self.set_site(sites.other(self.state.site))

    def set_language(self, language: str) -> None:
        preferences.set_language(language)  # language_changed re-themes and re-renders

    def set_theme_mode(self, mode: str) -> None:
        self.theme_override = ""
        self.state.theme_mode = preferences.set_theme_mode(mode)
        self.apply_theme()

    def set_cover_size(self, width: float, *, save: bool = True) -> None:
        """Every grid follows one cover width; a slider drag saves only when released."""
        width = card_size.clamp_card_width(round(width))
        self.state.cover_size = width
        if save:
            card_size.save_card_width(width)

    def toggle_theme(self) -> None:
        from .theme import resolve_mode

        self.set_theme_mode("light" if resolve_mode(self.page) == "dark" else "dark")

    def apply_theme(self) -> None:
        apply_theme(
            self.page,
            theme_mode=self.state.theme_mode,
            language=self.state.language,
            user_font=str(app_config.ui_font_family or ""),
        )
        self.page.update()

    def open_account(self) -> None:
        self.state.account_open = True

    def close_account(self) -> None:
        self.state.account_open = False

    # ── events from the core ─────────────────────────────────────────────────

    def connect(self) -> None:
        self.events.on("language_changed", self._on_language)
        self.events.on("font_changed", lambda: self.apply_theme())
        self.events.on("content_rating_changed", self._on_rating_changed)
        self.events.on("site_changed", self._on_site_changed)
        self.events.on("history_changed", self._on_history_changed, coalesce=lambda *_a: "history")
        self.events.on("login_state_changed", self._on_login_changed)
        self.events.on("shortcuts_changed", self.shortcuts.refresh)
        self.events.on("release_update_available", self._on_release)
        for name in ("subscription_sources_changed", "subscription_source_added"):
            self.events.on(name, self._reload_subscriptions, coalesce=lambda *_args: "subscriptions")
        # Background notices (subscription refresh): in the window, or a system notification when it is
        # in the background (services/system_notices.py); the subscription list is read again too.
        self.events.on("desktop_notification_requested", self._on_desktop_notification)
        # Finished / failed downloads: every transition (no coalescing), batched by SystemNotices.
        self.events.on("task_status_changed", self.notices.on_task_status)
        for name in ("rules_changed", "active_rule_changed", "page_rule_changed"):
            self.events.on(name, self.rules.refresh, coalesce=lambda *_args: "rules")
        # A finished download is a new library record (the old page re-read on completion too).
        self.events.on("task_status_changed", self._on_task_finished,
                       coalesce=lambda _task_id="", status="", *_a: ("library", status))  # one per status: "completed" survives
        for name in ("task_added", "tasks_added", "task_status_changed", "task_removed", "tasks_removed", "staging_changed"):
            self.events.on(name, self._schedule_badges, coalesce=lambda *_args: "badges")
        self.tasks.attach(self)

    def close(self) -> None:
        self.events.close()
        self.bridge.close()
        self.images.close()
        self.search.close()
        self.details.close()
        self.repair.close()
        self.notices.close()
        self.tools.close()
        self.library.flush()

    def _on_release(self, release: dict) -> None:
        offer = release_offer(release if isinstance(release, dict) else {})
        if offer is None:
            return

        async def ask() -> None:
            title, message, informative, open_label, _later = offer.question
            if await confirm(self.page, title, message, confirm_label=open_label, informative=informative):
                await ft.UrlLauncher().launch_url(offer.url)

        self.page.run_task(ask)

    def _on_rating_changed(self, _rating: str) -> None:
        self.state.rating = preferences.current_rating()
        self.page.run_task(self.search.rerun)
        self.home.set_rating(self.state.rating)

    def _on_login_changed(self, logged_in: bool) -> None:
        self.state.logged_in = bool(logged_in)
        self.home.on_login_changed()

    def _on_site_changed(self, _key: str) -> None:
        self.state.site = preferences.current_site()
        self.page.run_task(self.search.rerun)
        self._reload_subscriptions()

    def _on_history_changed(self, *_args) -> None:
        self.page.run_task(self.search.refresh_statuses)
        if self.library.state.loaded:
            self.page.run_task(self.library.load)

    def _on_task_finished(self, _task_id: str = "", status: str = "", *_args) -> None:
        if status == "completed" and self.library.state.loaded:
            self.page.run_task(self.library.load)

    def _reload_subscriptions(self, *_args) -> None:
        if self.subscriptions.state.loaded:
            self.page.run_task(self.subscriptions.load)

    def _on_desktop_notification(self, title: str = "", message: str = "", *_args) -> None:
        self.page.run_task(self.notices.show, str(title or ""), str(message or ""))
        self._reload_subscriptions()

    def _on_language(self, _code: str) -> None:
        self.state.language = current_language()
        self.apply_theme()

    def _schedule_badges(self, *_args) -> None:
        self.page.run_task(self.refresh_badges)

    async def refresh_badges(self) -> None:
        try:
            active, staged = await asyncio.to_thread(badge_counts, download_manager)
        except Exception:
            logger.exception("Could not count downloads for the navigation badge")
            return
        self.state.active_downloads, self.state.staged_downloads = active, staged
        await self._keep_awake(active > 0)

    @property
    def caps(self):
        """What this platform can do (``platform/capabilities.py``)."""
        return capabilities(self.state.layout)

    async def offer_copied_link(self) -> None:
        """Phones, back in the foreground: offer a newly copied Iwara link (no share intents in Flet)."""
        if not self.caps.mobile:
            return
        try:
            text = str(await ft.Clipboard().get() or "")
        except Exception:
            logger.debug("Clipboard unavailable", exc_info=True)
            return
        if not text or text == self._offered_clipboard:
            return
        self._offered_clipboard = text  # offer each copy once
        link = clipboard_links.copied_link(text)
        if link is None:
            return
        target = (routes.route(routes.AUTHOR, link.key) if link.kind == "profile"
                  else routes.route(routes.DETAIL, link.kind, link.key))
        from .services.notify import notice

        notice(self.page, "info", clipboard_links.offer_title(link), link.url,
               action=clipboard_links.open_label(), on_action=lambda: self.open(target), duration_ms=8000)

    async def _keep_awake(self, wanted: bool) -> None:
        """Phones: the app is suspended when the screen sleeps, so keep it on while downloads run (8.2)."""
        if not self.caps.keep_awake or wanted == self._awake:
            return
        self._awake = wanted
        try:
            if wanted:
                await ft.Wakelock().enable()
            else:
                await ft.Wakelock().disable()
        except Exception:  # an unsupported platform or a dev run with --platform
            logger.debug("Wakelock unavailable", exc_info=True)

    async def restore_login(self) -> None:
        try:
            await asyncio.to_thread(account.restore_login, download_manager)
        except Exception:
            logger.exception("Signing in at start-up failed")

    # ── keyboard ─────────────────────────────────────────────────────────────

    async def on_keyboard(self, event: ft.KeyboardEvent) -> None:
        if not self.state.layout.is_desktop:
            return
        chord = event_chord(event.key, shift=event.shift, ctrl=event.ctrl, alt=event.alt, meta=event.meta)
        if self.key_capture is not None:
            self.key_capture(chord, str(event.key or ""))
            return
        if self.state.destination == routes.TOOLS and self.tools.handle_key(chord):
            return
        action = self.shortcuts.action_for(chord)
        if chord == "Alt+Left":
            action = action or "nav_back"
        if action:
            await self.run_action(action)

    async def run_action(self, action: str) -> bool:
        """Global shortcut actions; page actions are handled by the pages (later phases)."""

        if action in _NAV_SHORTCUTS:
            destination = _NAV_SHORTCUTS[action]
            if destination in self.visible_destinations():
                self.navigator.switch(destination)
            return True
        if action == "nav_back":
            self.back()
        elif action == "nav_next":
            self.cycle_destination(1)
        elif action == "nav_prev":
            self.cycle_destination(-1)
        elif action == "rating_cycle":
            self.cycle_rating()
        elif action in ("card_size_up", "card_size_down"):
            self.set_cover_size(card_size.stepped_card_width(self.state.cover_size, 1 if action == "card_size_up" else -1))
        elif action == "card_size_reset":
            self.set_cover_size(card_size.CARD_WIDTH_DEFAULT)
        elif action == "toggle_site":
            self.toggle_site()
        elif action == "toggle_theme":
            self.toggle_theme()
        elif action == "toggle_fullscreen":
            self.page.window.full_screen = not self.page.window.full_screen
            self.page.update()
        elif action == "minimize_window":
            self.page.window.minimized = True
            self.page.update()
        elif action in ("close_window", "quit_app"):
            from .lifecycle import shutdown

            await shutdown(self.page)
        else:
            return False
        return True
