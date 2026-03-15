"""Centralized signal bus for cross-module UI↔backend communication."""
from PySide6.QtCore import QObject, Signal


class TaskSignalBus(QObject):
    # task_id, info_dict (title, author, video_id, status)
    task_added = Signal(str, dict)

    # task_id, new_status (str value of TaskStatus)
    task_status_changed = Signal(str, str)

    # task_id, downloaded_bytes, total_bytes, speed_str
    task_progress_updated = Signal(str, int, int, str)

    # task_id, error_message
    task_error = Signal(str, str)

    # task_id
    task_removed = Signal(str)

    # General log / info message for the UI
    log_message = Signal(str)

    # Emitted when login state changes (bool: logged_in)
    login_state_changed = Signal(bool)

    # Emitted when UI language changes (str: language code)
    language_changed = Signal(str)


# Module-level singleton — import this everywhere
signal_bus = TaskSignalBus()
