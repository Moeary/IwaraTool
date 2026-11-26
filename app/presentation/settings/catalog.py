"""Settings categories (docs/flet_ui_plan.md 7.11): what each one holds and is called.

Both interfaces list these in order; icons are each interface's own.  The
cards of a category are keys the interfaces build; the keyboard shortcut
cards are appended per scope by the interface that has them.
"""
from __future__ import annotations

from dataclasses import dataclass

from .texts import stext

CATEGORY_KEY = "settings_category_v1"  # the category shown last


@dataclass(frozen=True)
class Category:
    key: str
    title_key: str
    summary_key: str
    cards: tuple[str, ...]

    @property
    def title(self) -> str:
        return stext(self.title_key)

    @property
    def summary(self) -> str:
        return stext(self.summary_key)


CATEGORIES: tuple[Category, ...] = (
    Category("general", "general", "appearance_font_language_updates_and_where",
             ("appearance", "font", "language", "updates", "data_paths")),
    Category("home", "home", "choose_which_rows_the_home_page", ("home_layout", "home_cache")),
    Category("network", "account_network", "the_iwara_site_to_browse_iwara", ("site", "account", "proxy")),
    Category("downloads", "downloads", "save_location_quality_concurrency_speed_and",
             ("download_dir", "quality", "behavior", "concurrency", "download_policy", "aria2")),
    Category("search", "search", "search_history_download_limits_tag_dictionary",
             ("search_engine", "search_history", "search_limit", "search_bridge")),
    Category("subscriptions", "subscriptions_refresh", "automatic_subscription_refresh_subscribe_prompts",
             ("subscription_automation", "subscription_prompt", "cover_performance")),
    Category("tools", "tools", "the_ffmpeg_program_used_by_upload", ("ffmpeg",)),
    Category("system", "system_maintenance", "tray_and_startup_behaviour_video_playback",
             ("window", "card_clicks", "playback", "request_policy", "maintenance")),
    Category("shortcuts", "keyboard_shortcuts", "shortcuts_for_every_page_and_the", ()),
)

# Older links name a card rather than its category.
ALIASES = {"account": "network", "proxy": "network", "site": "network", "aria2": "downloads", "ffmpeg": "tools"}


def category(key: str) -> Category | None:
    key = ALIASES.get(key, key)
    return next((c for c in CATEGORIES if c.key == key), None)
