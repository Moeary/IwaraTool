"""Wires the shortcut catalogue to the main window and its pages."""
from __future__ import annotations

from .shortcuts import registry


def _focus(edit) -> None:
    edit.setFocus()
    edit.selectAll()


def install_shortcuts(window) -> None:
    """Bind every page-level action; called once the pages exist."""
    pages = [
        ("nav_home", window._home_page),
        ("nav_download", window._download_page),
        ("nav_search", window._search_page),
        ("nav_subscriptions", window._subscription_page),
        ("nav_history", window._history_page),
        ("nav_repair", window._repair_page),
        ("nav_rules", window._rules_page),
        ("nav_settings", window._settings_page),
    ]
    handlers = {
        action_id: (lambda page=page: window.switchTo(page)) for action_id, page in pages
    }
    handlers["quit_app"] = window._quit_from_tray
    registry.bind(window, handlers)

    home = window._home_page
    registry.bind(
        home,
        {
            "home_refresh": lambda: home.refresh(),
            "home_back": home.go_back,
            "home_customize": home.customize,
        },
    )

    download = window._download_page
    tasks = download._task_center
    registry.bind(
        download,
        {
            "download_submit": download._submit,
            "download_paste_submit": lambda: download._paste_from_clipboard(submit=True),
            "tasks_focus_search": lambda: _focus(tasks._search_edit),
            "tasks_retry_failed": tasks._retry_all_failed,
            "tasks_clear_done": tasks._clear_done,
        },
    )

    search = window._search_page
    registry.bind(
        search,
        {
            "search_focus": lambda: _focus(search._keyword_edit),
            "search_run": search._start_search,
            "search_queue": search._queue_selected,
            "search_preview": search._preview_selected,
            "search_open_page": search._open_selected,
        },
    )

    subscriptions = window._subscription_page
    registry.bind(
        subscriptions,
        {
            "sub_add": subscriptions._add_source,
            "sub_refresh": subscriptions._refresh_current_source,
            "sub_refresh_all": subscriptions._refresh_all,
            "sub_download_selected": subscriptions._download_selected,
            "sub_download_new": subscriptions._download_new,
        },
    )

    history = window._history_page
    registry.bind(
        history,
        {
            "history_focus_search": lambda: _focus(history._search_edit),
            "history_open_file": lambda: history._open_selected(open_file=True),
            "history_open_folder": lambda: history._open_selected(open_file=False),
            "history_preview": history._preview_selected,
            "history_remove": history._remove_selected_record,
            "history_export": history._export_visible_csv,
            "history_reload": history._load_history,
        },
    )

    repair = window._repair_page
    registry.bind(repair, {"repair_scan": repair._scan_folder, "repair_apply": repair._start_repair})

    rules = window._rules_page
    registry.bind(
        rules,
        {"rules_new": rules._new_rule, "rules_save": rules._save_rule, "rules_delete": rules._delete_rule},
    )

    settings = window._settings_page
    registry.bind(settings, {"settings_save": settings._save_settings})
