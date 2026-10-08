"""Wires the shortcut catalogue to the main window and its pages."""
from __future__ import annotations

from .media_card import reset_card_width, step_card_width
from .shortcuts import registry


def _index_of(window, cycle) -> int:
    current = window.stackedWidget.currentWidget()
    return cycle.index(current) if current in cycle else 0


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
    # Ctrl+Tab walks the sidebar in the order it is drawn.
    cycle = [
        window._home_page, window._subscription_page, window._search_page, window._download_page,
        window._repair_page, window._history_page, window._rules_page, window._settings_page,
    ]
    handlers.update({
        "nav_next": lambda: window.switchTo(cycle[(_index_of(window, cycle) + 1) % len(cycle)]),
        "nav_prev": lambda: window.switchTo(cycle[(_index_of(window, cycle) - 1) % len(cycle)]),
        "focus_search": lambda: (window.switchTo(window._search_page), window._search_page._focus_keyword()),
        "quick_download": window.quick_download,
        "open_download_folder": window.open_download_folder,
        "rating_cycle": window.cycle_rating,
        "card_size_up": lambda: step_card_width(1),
        "card_size_down": lambda: step_card_width(-1),
        "card_size_reset": reset_card_width,
        "toggle_theme": lambda: window._toggle_dark_mode(),
        "toggle_fullscreen": window.toggle_fullscreen,
        "shortcut_help": window.show_shortcut_help,
    })
    registry.bind(window, handlers)

    home = window._home_page
    registry.bind(
        home,
        {
            "home_refresh": lambda: home.refresh(),
            "home_back": home.go_back,
            "home_customize": home.customize,
            "home_top": lambda: home.scroll_to(bottom=False),
            "home_bottom": lambda: home.scroll_to(bottom=True),
            "home_fold_all": lambda: home.fold_all(True),
            "home_unfold_all": lambda: home.fold_all(False),
            "detail_close": home.close_current,
            "detail_like": lambda: home.detail_action("like"),
            "detail_play": lambda: home.detail_action("play"),
            "detail_download": lambda: home.detail_action("download"),
            "detail_open_browser": lambda: home.detail_action("browser"),
            "detail_copy_link": lambda: home.detail_action("copy"),
            "detail_author_page": lambda: home.detail_action("author"),
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
            "search_prev_page": search._go_previous_page_if_enabled,
            "search_next_page": search._go_next_page_if_enabled,
            "search_toggle_view": search._toggle_view_mode,
            "search_toggle_controls": search._toggle_search_controls,
            "search_reset": search._reset_filters,
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
            "sub_back": subscriptions.go_back_view,
            "sub_select_all": subscriptions.select_all_in_view,
            "sub_clear_selection": subscriptions.clear_selection_in_view,
            "sub_toggle_view": subscriptions.toggle_view_mode,
            "sub_focus_filter": subscriptions.focus_filter,
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
        {"rules_new": rules._new_rule, "rules_save": rules._save_rule_if_enabled, "rules_delete": rules._delete_rule},
    )

    settings = window._settings_page
    registry.bind(settings, {"settings_save": settings._save_settings})
