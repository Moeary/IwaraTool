"""Start and stop the app's services.

Order matters:

1. ``restore_pending_backup()`` before anything imports ``app.config``: a
   restore staged in Settings must land before a database or settings file
   is opened.
2. ``prepare(frontend=...)``: logging, proxy and request settings, the cached
   login, the temporary download rule.  The interface may then build its
   window.
3. ``start_services()``: restored download tasks resume, the background
   service (timed subscription refreshes, update checks) starts.
4. ``stop(wait=...)`` when the app closes: background service, download
   manager (active work is paused and saved for the next start), then every
   open settings file is written.  It is safe to call more than once.

``start()`` is ``prepare()`` + ``start_services()``.  All of these block, so
the Flet interface runs them with ``asyncio.to_thread``.
"""
from __future__ import annotations

import threading

_stop_lock = threading.Lock()
_stopped = False


def restore_pending_backup() -> None:
    from .core.backup import apply_pending_restore
    from .paths import app_data_dir

    apply_pending_restore(str(app_data_dir()))


def prepare(*, frontend: str) -> None:
    from .core.download.manager import download_manager
    from .core.rules import ensure_temporary_rule
    from .logging_setup import get_logger, setup_logging
    from .version import __version__

    setup_logging()
    get_logger(__name__).info("IwaraTool %s starting (%s interface)", __version__, frontend)
    download_manager.apply_config()
    # Restore the cached token first for a faster startup sign-in.
    download_manager.restore_cached_login()
    # Search / detail downloads default to this rule (own folder, no history).
    ensure_temporary_rule()


def start_services() -> None:
    from .core.background_services import background_service
    from .core.download.manager import download_manager

    download_manager.start()
    background_service.start()


def start(*, frontend: str) -> None:
    prepare(frontend=frontend)
    start_services()


def checkpoint() -> None:
    """Save the download queue and write settings without stopping anything.

    Phones never close a window: the system suspends the app and may end it
    without warning, so the interface calls this whenever the app is paused.
    """
    if _stopped:
        return
    from .core.download.manager import download_manager
    from .settings_store import sync_all_open_stores

    try:
        download_manager.flush_task_queue()
    finally:
        sync_all_open_stores()


def stop(*, wait: bool) -> None:
    """Stop background work, save the download queue and write settings (once)."""

    global _stopped
    with _stop_lock:
        if _stopped:
            return
        _stopped = True
    from .core.background_services import background_service
    from .core.download.manager import download_manager
    from .settings_store import sync_all_open_stores

    try:
        background_service.stop(wait=wait)
    finally:
        try:
            download_manager.shutdown(wait=wait)
        finally:
            sync_all_open_stores()
