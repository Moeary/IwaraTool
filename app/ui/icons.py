"""The one place the Flet interface picks icons.

Pages and components never write ``ft.Icons.X`` themselves (a test enforces
it): they ask for an icon by meaning, so one action shows the same glyph on
every page and a restyle is a change to this file alone.

Style rules:

* One icon set: Material Icons as bundled with Flet.  No Cupertino icons, no
  other icon fonts, no images standing in for icons.
* At rest an icon is the line (``_OUTLINED``) variant; a selected navigation
  destination or an active toggle (liked, subscribed) switches to the filled
  ``_ROUNDED`` variant.  Glyphs that are lines only (arrows, chevrons, close)
  look the same in both and use the plain or outlined name.
* Sizes come from ``tokens.Size`` (``ICON_SM`` 16, ``ICON`` 20, ``ICON_NAV`` 24).
"""

from __future__ import annotations

from dataclasses import dataclass

import flet as ft

_I = ft.Icons


@dataclass(frozen=True)
class IconPair:
    """An icon with a resting (line) and a selected or active (filled) form."""

    rest: ft.IconData
    active: ft.IconData

    def pick(self, active: bool) -> ft.IconData:
        return self.active if active else self.rest


class NavIcon:
    """Navigation destinations (rail, bottom bar, avatar menu)."""

    HOME = IconPair(_I.HOME_OUTLINED, _I.HOME_ROUNDED)
    SUBSCRIPTIONS = IconPair(_I.SUBSCRIPTIONS_OUTLINED, _I.SUBSCRIPTIONS_ROUNDED)
    SEARCH = IconPair(_I.SEARCH_OUTLINED, _I.MANAGE_SEARCH_ROUNDED)
    DOWNLOADS = IconPair(_I.DOWNLOAD_FOR_OFFLINE_OUTLINED, _I.DOWNLOAD_FOR_OFFLINE_ROUNDED)
    LIBRARY = IconPair(_I.VIDEO_LIBRARY_OUTLINED, _I.VIDEO_LIBRARY_ROUNDED)
    RULES = IconPair(_I.RULE_FOLDER_OUTLINED, _I.RULE_FOLDER_ROUNDED)
    REPAIR = IconPair(_I.AUTO_FIX_HIGH_OUTLINED, _I.AUTO_FIX_HIGH_ROUNDED)
    TOOLS = IconPair(_I.HANDYMAN_OUTLINED, _I.HANDYMAN_ROUNDED)
    SETTINGS = IconPair(_I.SETTINGS_OUTLINED, _I.SETTINGS_ROUNDED)
    ACCOUNT = IconPair(_I.ACCOUNT_CIRCLE_OUTLINED, _I.ACCOUNT_CIRCLE_ROUNDED)


class SettingsIcon:
    """Settings groups (presentation/settings/groups.py)."""

    APPEARANCE = IconPair(_I.PALETTE_OUTLINED, _I.PALETTE_ROUNDED)
    BROWSING = IconPair(_I.EXPLORE_OUTLINED, _I.EXPLORE_ROUNDED)
    STORAGE = IconPair(_I.STORAGE_OUTLINED, _I.STORAGE_ROUNDED)
    SYSTEM = IconPair(_I.TUNE_OUTLINED, _I.TUNE_ROUNDED)
    KEYBOARD = IconPair(_I.KEYBOARD_OUTLINED, _I.KEYBOARD_ROUNDED)
    ABOUT = IconPair(_I.INFO_OUTLINED, _I.INFO_ROUNDED)
    RATING = IconPair(_I.SHIELD_OUTLINED, _I.SHIELD_ROUNDED)  # the content-rating button


class StateIcon:
    """Toggles whose icon fills in while they are on."""

    LIKE = IconPair(_I.FAVORITE_BORDER_ROUNDED, _I.FAVORITE_ROUNDED)
    SUBSCRIBE = IconPair(_I.NOTIFICATIONS_NONE_ROUNDED, _I.NOTIFICATIONS_ACTIVE_ROUNDED)
    FOLLOW = IconPair(_I.PERSON_ADD_ALT_OUTLINED, _I.HOW_TO_REG_ROUNDED)
    SELECT = IconPair(_I.RADIO_BUTTON_UNCHECKED_ROUNDED, _I.CHECK_CIRCLE_ROUNDED)
    CHECKBOX = IconPair(_I.CHECK_BOX_OUTLINE_BLANK_ROUNDED, _I.CHECK_BOX_ROUNDED)
    EXPAND = IconPair(_I.EXPAND_MORE_ROUNDED, _I.EXPAND_LESS_ROUNDED)
    RAIL = IconPair(_I.MENU_ROUNDED, _I.MENU_OPEN_ROUNDED)
    FULLSCREEN = IconPair(_I.FULLSCREEN_ROUNDED, _I.FULLSCREEN_EXIT_ROUNDED)
    MUTE = IconPair(_I.VOLUME_UP_OUTLINED, _I.VOLUME_OFF_ROUNDED)
    PLAY_PAUSE = IconPair(_I.PLAY_ARROW_ROUNDED, _I.PAUSE_ROUNDED)


class AppIcon:
    """Actions and markers, by meaning."""

    # Navigation within a page
    BACK = _I.ARROW_BACK_ROUNDED
    FORWARD = _I.ARROW_FORWARD_ROUNDED
    CLOSE = _I.CLOSE_ROUNDED
    MENU = _I.MENU_ROUNDED
    MORE = _I.MORE_VERT_ROUNDED
    MORE_HORIZ = _I.MORE_HORIZ_ROUNDED
    CHEVRON_LEFT = _I.CHEVRON_LEFT_ROUNDED
    CHEVRON_RIGHT = _I.CHEVRON_RIGHT_ROUNDED
    FIRST_PAGE = _I.FIRST_PAGE_ROUNDED
    LAST_PAGE = _I.LAST_PAGE_ROUNDED
    TO_TOP = _I.VERTICAL_ALIGN_TOP_ROUNDED
    OPEN_IN_NEW = _I.OPEN_IN_NEW_ROUNDED

    # Editing
    ADD = _I.ADD_ROUNDED
    REMOVE = _I.REMOVE_ROUNDED
    EDIT = _I.EDIT_OUTLINED
    DELETE = _I.DELETE_OUTLINE_ROUNDED
    SAVE = _I.SAVE_OUTLINED
    UNDO = _I.UNDO_ROUNDED
    COPY = _I.CONTENT_COPY_OUTLINED
    PASTE = _I.CONTENT_PASTE_ROUNDED
    CLEAR = _I.CLEAR_ROUNDED
    CHECK = _I.CHECK_ROUNDED
    SELECT_ALL = _I.SELECT_ALL_ROUNDED
    DRAG = _I.DRAG_INDICATOR_ROUNDED
    TUNE = _I.TUNE_ROUNDED

    # Finding and arranging
    SEARCH = _I.SEARCH_ROUNDED
    FILTER = _I.FILTER_LIST_ROUNDED
    SORT = _I.SORT_ROUNDED
    SORT_ASC = _I.ARROW_UPWARD_ROUNDED
    SORT_DESC = _I.ARROW_DOWNWARD_ROUNDED
    GRID_VIEW = _I.GRID_VIEW_OUTLINED
    LIST_VIEW = _I.VIEW_AGENDA_OUTLINED
    TABLE_VIEW = _I.TABLE_ROWS_OUTLINED
    ZOOM_IN = _I.ZOOM_IN_ROUNDED
    ZOOM_OUT = _I.ZOOM_OUT_ROUNDED
    HISTORY = _I.HISTORY_ROUNDED

    # Downloads and tasks
    DOWNLOAD = _I.DOWNLOAD_ROUNDED
    UPLOAD = _I.UPLOAD_ROUNDED
    REFRESH = _I.REFRESH_ROUNDED
    SYNC = _I.SYNC_ROUNDED
    RETRY = _I.REPLAY_ROUNDED
    PLAY = _I.PLAY_ARROW_ROUNDED
    PAUSE = _I.PAUSE_ROUNDED
    STOP = _I.STOP_ROUNDED
    CANCEL = _I.CANCEL_OUTLINED
    PRIORITY = _I.LOW_PRIORITY_ROUNDED
    QUEUED = _I.SCHEDULE_ROUNDED
    SPEED = _I.SPEED_ROUNDED
    LOG = _I.RECEIPT_LONG_OUTLINED
    REMOTE = _I.CLOUD_OUTLINED
    CLEAN = _I.CLEANING_SERVICES_OUTLINED

    # Files and links
    FOLDER = _I.FOLDER_OUTLINED
    FOLDER_OPEN = _I.FOLDER_OPEN_OUTLINED
    FILE = _I.INSERT_DRIVE_FILE_OUTLINED
    LINK = _I.LINK_ROUNDED
    SHARE = _I.SHARE_OUTLINED
    EXPORT = _I.IOS_SHARE_ROUNDED
    BROWSER = _I.PUBLIC_ROUNDED

    # Content
    VIDEO = _I.MOVIE_OUTLINED
    IMAGE = _I.IMAGE_OUTLINED
    PLAYLIST = _I.PLAYLIST_PLAY_ROUNDED
    SKIP_NEXT = _I.SKIP_NEXT_ROUNDED
    SKIP_PREVIOUS = _I.SKIP_PREVIOUS_ROUNDED
    ENLARGE = _I.OPEN_IN_FULL_ROUNDED
    WATCHED = _I.TASK_ALT_ROUNDED
    LOCATE = _I.FIND_IN_PAGE_OUTLINED
    TAG = _I.SELL_OUTLINED
    AUTHOR = _I.PERSON_OUTLINE_ROUNDED
    AUTHORS = _I.GROUP_OUTLINED
    VIEWS = _I.VISIBILITY_OUTLINED
    LIKES = _I.FAVORITE_BORDER_ROUNDED
    COMMENTS = _I.CHAT_BUBBLE_OUTLINE_ROUNDED
    DURATION = _I.SCHEDULE_ROUNDED
    DATE = _I.CALENDAR_TODAY_OUTLINED
    NEW = _I.FIBER_NEW_OUTLINED

    # Global preferences and account
    SITE = _I.LANGUAGE_ROUNDED
    LANGUAGE = _I.TRANSLATE_ROUNDED
    RATING = _I.SHIELD_OUTLINED
    THEME_SYSTEM = _I.BRIGHTNESS_AUTO_OUTLINED
    THEME_LIGHT = _I.LIGHT_MODE_OUTLINED
    THEME_DARK = _I.DARK_MODE_OUTLINED
    LOGIN = _I.LOGIN_ROUNDED
    LOGOUT = _I.LOGOUT_ROUNDED
    KEYBOARD = _I.KEYBOARD_OUTLINED
    ABOUT = _I.INFO_OUTLINE_ROUNDED
    SOURCE_CODE = _I.CODE_ROUNDED

    # Feedback
    INFO = _I.INFO_OUTLINE_ROUNDED
    SUCCESS = _I.CHECK_CIRCLE_OUTLINE_ROUNDED
    WARNING = _I.WARNING_AMBER_ROUNDED
    ERROR = _I.ERROR_OUTLINE_ROUNDED
    HELP = _I.HELP_OUTLINE_ROUNDED
    EMPTY = _I.INBOX_OUTLINED
    OFFLINE = _I.CLOUD_OFF_OUTLINED


def all_icons() -> dict[str, ft.IconData]:
    """Every registered glyph by dotted name (``NavIcon.HOME.rest`` …), for tests and the gallery."""

    result: dict[str, ft.IconData] = {}
    for group in (NavIcon, StateIcon, AppIcon):
        for name, value in vars(group).items():
            if name.startswith("_"):
                continue
            if isinstance(value, IconPair):
                result[f"{group.__name__}.{name}.rest"] = value.rest
                result[f"{group.__name__}.{name}.active"] = value.active
            elif isinstance(value, ft.IconData):
                result[f"{group.__name__}.{name}"] = value
    return result
