"""Centralized signal bus for cross-module UI↔backend communication."""
from PySide6.QtCore import QObject, Signal


class TaskSignalBus(QObject):
    # task_id, info_dict (title, author, video_id, status)
    task_added = Signal(str, dict)

    # list[dict] with task_id, title, author, video_id, status
    tasks_added = Signal(list)

    # task_id, new_status (str value of TaskStatus)
    task_status_changed = Signal(str, str)

    # task_id, downloaded_bytes, total_bytes, speed_str
    task_progress_updated = Signal(str, int, int, str)

    # task_id, error_message
    task_error = Signal(str, str)

    # task_id
    task_removed = Signal(str)

    # task_id, priority
    task_priority_changed = Signal(str, int)

    # list[str]
    tasks_removed = Signal(list)

    # General log / info message for the UI
    log_message = Signal(str)

    # Emitted when login state changes (bool: logged_in)
    login_state_changed = Signal(bool)

    # Emitted when UI language changes (str: language code)
    language_changed = Signal(str)

    # Emitted when shared download options change from any page.
    download_options_changed = Signal()

    # source_id
    subscription_source_added = Signal(int)

    # source_id; explicitly navigate to an existing subscription source.
    subscription_source_requested = Signal(int)

    # Download history or its local file paths changed after a committed write.
    history_changed = Signal()

    # Emitted when named download/filter rules are added, edited, or deleted.
    rules_changed = Signal()

    # rule_id; keeps rule pickers synchronized across pages.
    active_rule_changed = Signal(str)

    # title, message
    desktop_notification_requested = Signal(str, str)

    # GitHub Release payload
    release_update_available = Signal(dict)

    # A keyboard shortcut was reassigned or reset.
    shortcuts_changed = Signal()

    # video id, title, local file path (empty when the video is not on disk)
    video_preview_requested = Signal(str, str, str)

    # SFW/NSFW selection (core.rating value) changed on Home or Search.
    content_rating_changed = Signal(str)

    # kind ("video" / "image"), id; show that post in the Home detail view.
    media_detail_requested = Signal(str, str)

    # {"scope", "keyword", "sort", "author": (username, name, id, avatar)};
    # run an Iwara search on the Search page.
    search_requested = Signal(dict)

    # Runtime automation/download policy settings were changed.
    background_settings_changed = Signal()


# Module-level singleton — import this everywhere
signal_bus = TaskSignalBus()
