"""The settings cards, keyed like the old page's cards.

Texts, choices, clamping and side effects come from
``presentation.settings`` (shared with the PySide6 page); each card reads
``app_config`` when it renders and re-renders after saving.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import flet as ft

from ....config import app_config
from ....core import account, backup, login_items
from ....core import site as sites
from ....core.home.cache import DEFAULT_CACHE_MINUTES, HOME_CACHE_MINUTES_KEY, shared_cache
from ....core.home.feed import default_specs, load_specs, save_specs
from ....core.playback import video_player
from ....core.rating import RATINGS, rating_label
from ....core.rules import rule_store
from ....i18n import tr
from ....version import __version__
from ....logging_setup import get_logger, log_dir
from ....presentation import preferences
from ....presentation import shortcuts as keys
from ....presentation.click_actions import ACTIONS as CLICK_ACTIONS, action_label
from ....presentation.fonts import build_font_stack, current_platform
from ....presentation.settings import groups, values as sv
from ....presentation.settings.texts import stext
from ... import layout, routes
from ...icons import AppIcon
from ...services.classic_import import import_classic
from ...shortcuts import HANDLED
from ...services.save_file import save_made_file
from ...services.dialogs import confirm
from ...platform import tray
from ...services.notify import notice
from ...stores.app_state import use_shell
from ...theme import palette_for, text_style
from ...tokens import TYPE, Size, Space
from .rows import buttons, card, dropdown, hint, number_field, row, switch_row, text_field, use_refresh

logger = get_logger(__name__)


def _compact(shell) -> bool:
    return shell.state.layout.width_class == layout.WIDTH_COMPACT


def _signal(name: str, *args) -> None:
    from ....signal_bus import signal_bus

    getattr(signal_bus, name).emit(*args)


async def _open_folder(path: str) -> None:
    if path and os.path.isdir(path):
        await ft.UrlLauncher().launch_url(Path(path).resolve().as_uri())


async def _pick_folder(title: str, initial: str = "") -> str:
    return str(await ft.FilePicker().get_directory_path(dialog_title=title, initial_directory=initial or None) or "")


async def _pick_file(title: str, extensions: list[str] | None = None) -> str:
    kind = ft.FilePickerFileType.CUSTOM if extensions else ft.FilePickerFileType.ANY
    files = await ft.FilePicker().pick_files(dialog_title=title, file_type=kind, allowed_extensions=extensions)
    return str(files[0].path or "") if files else ""


# ── general ──────────────────────────────────────────────────────────────────


@ft.component
def AppearanceCard():
    shell = use_shell()
    page = shell.page
    options = [("auto", stext("follow_system")), ("light", stext("light")), ("dark", stext("dark"))]
    return card(page, stext("appearance"), [
        row(page, stext("theme"), dropdown(shell.state.theme_mode, options, shell.set_theme_mode, width=200),
            help=stext("light_and_dark_modes_share_one"), compact=_compact(shell)),
    ])


@ft.component
def FontCard():
    shell = use_shell()
    page = shell.page
    tick, refresh = use_refresh()
    chosen = str(app_config.ui_font_family or "").strip()
    stack = build_font_stack(current_platform(), shell.state.language, user_family=chosen)

    def set_family(value: str) -> None:
        sv.set_font_family(value)
        refresh()

    return card(page, stext("font"), [
        row(page, stext("interface_font"), ft.Row(tight=True, spacing=Space.SM, controls=[
            text_field(chosen, set_family, width=220, hint_text=stext("automatic_2"), key=f"font:{tick}"),
            ft.TextButton(content=stext("automatic"), disabled=not chosen, on_click=lambda _e: set_family("")),
        ]), compact=True),  # the field and its button under the label: beside it they squeeze the label to a sliver
        hint(page, stext("automatic_picks_the_system_ui_fonts")),
        hint(page, stext("in_use_in_order") + " → ".join(stack)),
    ])


@ft.component
def LanguageCard():
    shell = use_shell()
    page = shell.page
    options = [(code, preferences.language_label(code)) for code in preferences.LANGUAGES]
    return card(page, stext("interface_language"), [
        row(page, stext("interface_language"), dropdown(shell.state.language, options, shell.set_language, width=200),
            help=stext("default_is_chinese_language_switch_is"), compact=_compact(shell)),
    ])


@ft.component
def LookCard():
    """Theme, language and font in one card (the group is already called Appearance)."""
    shell = use_shell()
    page = shell.page
    tick, refresh = use_refresh()
    compact = _compact(shell)
    chosen = str(app_config.ui_font_family or "").strip()
    stack = build_font_stack(current_platform(), shell.state.language, user_family=chosen)
    themes = [("auto", stext("follow_system")), ("light", stext("light")), ("dark", stext("dark"))]
    languages = [(code, preferences.language_label(code)) for code in preferences.LANGUAGES]

    def set_family(value: str) -> None:
        sv.set_font_family(value)
        refresh()

    return card(page, "", [
        row(page, stext("theme"), dropdown(shell.state.theme_mode, themes, shell.set_theme_mode, width=200),
            help=stext("light_and_dark_modes_share_one"), compact=compact),
        ft.Divider(height=1),
        row(page, stext("interface_language"), dropdown(shell.state.language, languages, shell.set_language, width=200),
            help=stext("default_is_chinese_language_switch_is"), compact=compact),
        ft.Divider(height=1),
        row(page, stext("interface_font"), ft.Row(tight=True, spacing=Space.SM, controls=[
            text_field(chosen, set_family, width=200, hint_text=stext("automatic_2"), key=f"font:{tick}"),
            ft.TextButton(content=stext("automatic"), disabled=not chosen, on_click=lambda _e: set_family("")),
        ]), help=stext("automatic_picks_the_system_ui_fonts"), compact=True),  # stacked: see FontCard
        hint(page, stext("in_use_in_order") + " → ".join(stack)),
    ])


@ft.component
def ContentSourceCard():
    """Which site and which rating: what every list shows."""
    shell = use_shell()
    page = shell.page
    texts = groups.about_texts()
    compact = _compact(shell)
    site_options = [(key, sites.SITES[key].label) for key in (sites.SITE_TV, sites.SITE_AI)]
    rating_options = [(value, rating_label(value)) for value in RATINGS]
    return card(page, "", [
        row(page, stext("site"), dropdown(shell.state.site, site_options, shell.set_site, width=200),
            help=stext("the_site_home_search_and_subscriptions"), compact=compact),
        ft.Divider(height=1),
        row(page, texts["rating_title"], dropdown(shell.state.rating, rating_options, shell.set_rating, width=200),
            help=texts["rating_help"], compact=compact),
    ])


@ft.component
def UpdatesCard():
    shell = use_shell()
    page = shell.page
    _tick, refresh = use_refresh()

    def toggle(value: bool) -> None:
        app_config.update_check_enabled = value
        sv.background_changed(shell.manager)
        refresh()

    def check(_e) -> None:
        from ....core.background_services import background_service

        background_service.check_updates_now()

    return card(page, stext("github_release_updates"), [
        switch_row(page, stext("github_release_updates"), app_config.update_check_enabled, toggle),
        buttons(ft.OutlinedButton(content=stext("check_now"), icon=AppIcon.REFRESH, on_click=check)),
    ])


@ft.component
def DataPathsCard():
    shell = use_shell()
    page = shell.page
    lines = [(stext("data_dir"), app_config.app_data_dir), (stext("config_file"), app_config.config_path),
             (stext("history_db"), app_config.history_db_path)]
    return card(page, stext("local_data_paths"), [
        *[ft.Text(f"{label}: {path}", style=text_style(TYPE.meta), selectable=True) for label, path in lines],
        buttons(ft.OutlinedButton(content=stext("open_data_folder"), icon=AppIcon.FOLDER_OPEN,
                                  on_click=lambda _e: page.run_task(_open_folder, app_config.app_data_dir)))
        if shell.caps.open_folder else None,
    ])


# ── home ─────────────────────────────────────────────────────────────────────


@ft.component
def HomeLayoutCard():
    shell = use_shell()
    page = shell.page
    shown = [spec.display_title() for spec in load_specs() if spec.enabled]

    async def reset() -> None:
        if await confirm(page, stext("reset_home"), stext("replace_your_home_rows_with_the"), confirm_label=stext("reset_to_default")):
            save_specs(default_specs())
            _signal("home_layout_changed")

    return card(page, stext("home_rows"), [
        hint(page, stext("showing") + (" · ".join(shown) if shown else stext("nothing"))),
        buttons(
            ft.FilledButton(content=stext("customize_rows"), icon=AppIcon.EDIT,
                            on_click=lambda _e: shell.open(routes.route(routes.SETTINGS, "home", "rows"))),
            ft.OutlinedButton(content=stext("reset_to_default"), on_click=lambda _e: page.run_task(reset)),
        ),
    ], description=stext("rows_can_be_the_newest_uploads"))


@ft.component
def HomeCacheCard():
    shell = use_shell()
    page = shell.page
    tick, refresh = use_refresh()
    minutes = sv.ui_int(HOME_CACHE_MINUTES_KEY, DEFAULT_CACHE_MINUTES, 0, 24 * 60)

    def set_minutes(text: str) -> None:
        app_config.set_ui_value(HOME_CACHE_MINUTES_KEY, sv.parse_int(text, current=minutes, low=0, high=24 * 60))
        refresh()

    async def clear() -> None:
        removed = await asyncio.to_thread(shared_cache().clear)
        _signal("home_layout_changed")  # rows reload from the site
        notice(page, "success", stext("home_cache_cleared"), sv.home_cache_cleared_text(removed))

    return card(page, stext("home_cache"), [
        row(page, stext("re_check_a_row_after_minutes"), number_field(minutes, set_minutes, key=f"minutes:{tick}"),
            compact=_compact(shell)),
        buttons(ft.OutlinedButton(content=stext("clear_home_cache"), icon=AppIcon.DELETE, on_click=lambda _e: page.run_task(clear))),
    ], description=stext("rows_are_kept_on_disk_and"))


# ── account and network ──────────────────────────────────────────────────────


@ft.component
def SiteCard():
    shell = use_shell()
    page = shell.page
    options = [(key, sites.SITES[key].label) for key in (sites.SITE_TV, sites.SITE_AI)]
    return card(page, stext("site"), [
        row(page, stext("site"), dropdown(shell.state.site, options, shell.set_site, width=200),
            help=stext("the_site_home_search_and_subscriptions"), compact=_compact(shell)),
    ])


@ft.component
def RatingCard():
    shell = use_shell()
    page = shell.page
    texts = groups.about_texts()
    options = [(value, rating_label(value)) for value in RATINGS]
    return card(page, texts["rating_title"], [
        row(page, texts["rating_title"], dropdown(shell.state.rating, options, shell.set_rating, width=200),
            help=texts["rating_help"], compact=_compact(shell)),
    ])


@ft.component
def AboutCard():
    shell = use_shell()
    page = shell.page
    palette = palette_for(page)
    texts = groups.about_texts()

    def link(label: str, url: str, icon) -> ft.Control:
        return ft.OutlinedButton(content=label, icon=icon, on_click=lambda _e: page.run_task(ft.UrlLauncher().launch_url, url))

    _tick, refresh = use_refresh()

    def toggle(value: bool) -> None:
        app_config.update_check_enabled = value
        sv.background_changed(shell.manager)
        refresh()

    def check(_e) -> None:
        from ....core.background_services import background_service

        background_service.check_updates_now()

    lines = [(texts["version"], __version__), *groups.about_lines()]
    updates = [
        ft.Divider(height=1),
        switch_row(page, stext("github_release_updates"), app_config.update_check_enabled, toggle),
        buttons(ft.OutlinedButton(content=stext("check_now"), icon=AppIcon.REFRESH, on_click=check)),
    ]
    return card(page, texts["title"], [
        hint(page, texts["tagline"]),
        *[ft.Row(spacing=Space.LG, controls=[
            ft.Container(width=Size.SETTINGS_LABEL, content=ft.Text(label, style=text_style(TYPE.body, color=palette.on_surface_variant))),
            ft.Text(value, style=text_style(TYPE.body), selectable=True, expand=True),
        ]) for label, value in lines],
        buttons(
            link(texts["repository"], groups.REPOSITORY_URL, AppIcon.BROWSER),
            link(texts["releases"], groups.RELEASES_URL, AppIcon.NEW),
            link(texts["issues"], groups.REPOSITORY_URL + "/issues", AppIcon.ABOUT),
        ),
        *updates,
    ])


@ft.component
def AccountCard():
    shell = use_shell()
    page = shell.page
    _tick, refresh = use_refresh()
    credential, set_credential = ft.use_state(str(app_config.username or ""))
    password, set_password = ft.use_state(str(app_config.password or ""))
    busy, set_busy = ft.use_state(False)
    status, set_status = ft.use_state("")
    logged_in = shell.state.logged_in

    def toggle(value: bool) -> None:
        sv.set_auth_enabled(value, shell.manager)
        if not value:
            set_status("")
        refresh()

    async def login() -> None:
        name, secret = credential.strip(), password
        if not name or not secret:
            notice(page, "warning", stext("notice"), stext("please_enter_username_email_and_password"))
            return
        if not app_config.api_proxy_enabled:
            notice(page, "warning", stext("api_proxy_is_off"), stext("if_tun_system_proxy_is_not"))
        set_busy(True)
        set_status(stext("signing_in"))
        try:
            ok, message = await asyncio.to_thread(account.login, shell.manager, name, secret)
        except Exception as exc:  # the network can fail in many ways
            ok, message = False, str(exc)
        set_busy(False)
        if ok:
            set_status("")
            notice(page, "success", stext("login_successful"), stext("signed_in_2"))
        else:
            set_status(sv.login_failed_text(message))
            notice(page, "error", stext("login_failed"), message or stext("please_check_username_and_password"))

    async def logout() -> None:
        await asyncio.to_thread(account.logout, shell.manager)
        notice(page, "info", stext("logged_out"))

    rows: list[ft.Control | None] = [
        switch_row(page, stext("account_login"), app_config.auth_enabled, toggle, help=stext("private_videos_require_login")),
    ]
    if app_config.auth_enabled:
        if logged_in:
            rows.append(ft.Row(spacing=Space.MD, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
                ft.Text(f"{stext('signed_in')} · {app_config.username}", style=text_style(TYPE.body), expand=True),
                ft.OutlinedButton(content=stext("logout"), icon=AppIcon.LOGOUT, on_click=lambda _e: page.run_task(logout)),
            ]))
        else:
            rows += [
                ft.TextField(label=stext("username"), value=credential, dense=True, disabled=busy,
                             on_change=lambda e: set_credential(e.control.value or "")),
                ft.TextField(label=stext("password"), value=password, dense=True, password=True, can_reveal_password=True,
                             disabled=busy, on_change=lambda e: set_password(e.control.value or ""),
                             on_submit=lambda _e: page.run_task(login)),
                hint(page, status, danger=status.startswith("✗")) if status else None,
                buttons(ft.FilledButton(content=stext("login"), icon=AppIcon.LOGIN, disabled=busy,
                                        on_click=lambda _e: page.run_task(login))),
            ]
    return card(page, stext("account_login"), rows)


@ft.component
def ProxyCard():
    shell = use_shell()
    page = shell.page
    api_on, set_api_on = ft.use_state(bool(app_config.api_proxy_enabled))
    api_url, set_api_url = ft.use_state(str(app_config.api_proxy_url or ""))
    dl_on, set_dl_on = ft.use_state(bool(app_config.download_proxy_enabled))
    dl_url, set_dl_url = ft.use_state(str(app_config.download_proxy_url or ""))
    saved = (bool(app_config.api_proxy_enabled), str(app_config.api_proxy_url or ""),
             bool(app_config.download_proxy_enabled), str(app_config.download_proxy_url or ""))
    dirty = (api_on, api_url, dl_on, dl_url) != saved

    def apply(_e=None) -> None:
        title, content = sv.apply_proxies(shell.manager, api_enabled=api_on, api_url=api_url,
                                          download_enabled=dl_on, download_url=dl_url)
        set_api_url(app_config.api_proxy_url)
        set_dl_url(app_config.download_proxy_url)
        notice(page, "success", title, content)

    def url(value: str, setter, enabled: bool) -> ft.Control:
        return ft.TextField(value=value, dense=True, hint_text="http://127.0.0.1:7890", disabled=not enabled,
                            on_change=lambda e: setter(e.control.value or ""), on_submit=apply)

    return card(page, stext("proxy"), [
        switch_row(page, stext("login_parsing_proxy"), api_on, set_api_on, help=stext("api_proxy_is_used_for_login")),
        url(api_url, set_api_url, api_on),
        switch_row(page, stext("video_download_proxy"), dl_on, set_dl_on, help=stext("only_affects_large_file_downloads_and")),
        url(dl_url, set_dl_url, dl_on),
        buttons(ft.FilledButton(content=stext("apply"), icon=AppIcon.CHECK, disabled=not dirty, on_click=apply)),
    ])


# ── downloads ────────────────────────────────────────────────────────────────


@ft.component
def DownloadDirCard():
    shell = use_shell()
    page = shell.page
    tick, refresh = use_refresh()
    error, set_error = ft.use_state("")
    folder = str(app_config.download_dir or "")

    def set_folder(text: str) -> None:
        path = text.strip()
        parent = os.path.dirname(os.path.abspath(path)) if path else ""
        if not path or not (os.path.isdir(path) or os.path.isdir(parent)):
            set_error(stext("folder_not_found"))
            return
        set_error("")
        app_config.download_dir = path
        shell.manager.apply_config()
        refresh()

    async def browse() -> None:
        path = await _pick_folder(stext("choose_download_directory_2"), folder)
        if path:
            set_folder(path)

    async def clean() -> None:
        if not await confirm(page, stext("confirm_cleanup"), stext("all_temp_files_in_download_directory"),
                             informative=stext("this_action_cannot_be_undone_continue"),
                             confirm_label=stext("delete_temp_files"), danger=True):
            return
        removed, failed = await asyncio.to_thread(shell.manager.clear_temp_files)
        ok, title, content, log = sv.temp_cleanup_report(removed, failed)
        _signal("log_message", log)
        notice(page, "success" if ok else "warning", title, content)

    return card(page, stext("download_directory"), [
        ft.Row(controls=[text_field(folder, set_folder, error=error, key=f"dir:{tick}")]),
        buttons(
            ft.OutlinedButton(content=stext("browse"), icon=AppIcon.FOLDER, on_click=lambda _e: page.run_task(browse)),
            ft.OutlinedButton(content=stext("open_download_folder"), icon=AppIcon.FOLDER_OPEN,
                          on_click=lambda _e: page.run_task(_open_folder, folder)) if shell.caps.open_folder else None,
            ft.OutlinedButton(content=stext("clean_temp"), icon=AppIcon.CLEAN, on_click=lambda _e: page.run_task(clean)),
        ),
        hint(page, stext("clean_stale_temp_files_in_download")),
    ], description=stext("video_files_are_saved_here_and"))


@ft.component
def QualityCard():
    shell = use_shell()
    page = shell.page
    _tick, refresh = use_refresh()
    return card(page, stext("preferred_quality"), [
        row(page, stext("preferred_quality"),
            dropdown(app_config.preferred_quality, sv.quality_choices(), lambda v: (sv.set_quality(v), refresh()), width=200),
            help=stext("if_preferred_quality_is_unavailable_fallback"), compact=_compact(shell)),
    ])


@ft.component
def BehaviorCard():
    shell = use_shell()
    page = shell.page
    _tick, refresh = use_refresh()

    def put(name: str, value) -> None:
        setattr(app_config, name, value)
        refresh()

    clicks = [("folder", stext("open_folder")), ("player", stext("open_player"))]
    action = "player" if str(app_config.completed_task_click_action or "").lower() == "player" else "folder"
    return card(page, stext("download_behavior"), [
        switch_row(page, stext("skip_existing_completed_files_in_batch"), app_config.skip_existing_files,
                   lambda v: put("skip_existing_files", v)),
        switch_row(page, stext("download_a_single_video_at_once"), bool(app_config.direct_download_single),
                   lambda v: put("direct_download_single", v)),
        # Phones open the library record instead (no file manager, 8.3).
        row(page, stext("completed_card_click_action"),
            dropdown(action, clicks, lambda v: put("completed_task_click_action", v), width=200), compact=_compact(shell))
        if shell.caps.open_folder else None,
    ])


@ft.component
def ConcurrencyCard():
    shell = use_shell()
    page = shell.page
    tick, refresh = use_refresh()
    low, high = sv.CONCURRENCY_RANGE
    retry_on = bool(app_config.auto_retry_failed_enabled)

    def put(name: str, value) -> None:
        setattr(app_config, name, value)
        refresh()

    rows: list[ft.Control | None] = [
        row(page, stext("max_tasks_in_resolving_queued_downloading"), ft.Slider(
            min=low, max=high, divisions=high - low, value=app_config.max_concurrent, label="{value}", width=260,
            on_change_end=lambda e: (sv.set_max_concurrent(int(e.control.value)), refresh())), compact=_compact(shell)),
        row(page, tr("Connections per file (built-in downloader)", "每个文件的连接数（内置下载器）",
                     "ファイルごとの接続数（内蔵ダウンローダー）"),
            dropdown(str(app_config.native_download_connections), [(str(n), str(n)) for n in (1, 2, 4, 8, 16)],
                     lambda v: put("native_download_connections", int(v or 1)), width=120),
            help=tr("Large files are fetched in that many parts at once and resume part by part. "
                    "Not used while aria2 downloads.",
                    "大文件分成这么多段同时下载，中断后按段续传。启用 aria2 时不使用。",
                    "大きなファイルをこの数に分けて同時に取得し、中断後は部分ごとに再開します。aria2 使用中は無効です。"),
            compact=_compact(shell)),
        row(page, stext("auto_cancel_idle_task_after_seconds"),
            number_field(app_config.task_stall_timeout_seconds, lambda t: (sv.set_stall_timeout(t), refresh()), key=f"stall:{tick}"),
            compact=_compact(shell)),
        switch_row(page, stext("auto_restore_idle_cancelled_tasks_when"), app_config.auto_restore_stalled_cancelled,
                   lambda v: put("auto_restore_stalled_cancelled", v), help=stext("only_restores_tasks_cancelled_by_the")),
        switch_row(page, stext("auto_retry_failed_tasks_with_network"), retry_on,
                   lambda v: put("auto_retry_failed_enabled", v), help=stext("broken_connections_incompleteread_timeouts_and_5")),
    ]
    if retry_on:
        attempts = app_config.auto_retry_failed_max_attempts
        rows += [
            row(page, stext("max_automatic_retries_per_task"), number_field(attempts, lambda t: put(
                "auto_retry_failed_max_attempts", sv.parse_int(t, current=attempts, low=sv.RETRY_ATTEMPTS_RANGE[0],
                                                               high=sv.RETRY_ATTEMPTS_RANGE[1])), key=f"att:{tick}"),
                compact=_compact(shell)),
            text_field(app_config.auto_retry_failed_retry_keywords,
                       lambda t: (sv.set_retry_keywords(t, app_config.auto_retry_failed_skip_keywords), refresh()),
                       label=stext("always_retry_errors_containing"), hint_text=stext("e_g_http_403_ssl"), key=f"retry:{tick}"),
            text_field(app_config.auto_retry_failed_skip_keywords,
                       lambda t: (sv.set_retry_keywords(app_config.auto_retry_failed_retry_keywords, t), refresh()),
                       label=stext("never_retry_errors_containing"), hint_text=stext("e_g_404"), key=f"skip:{tick}"),
            hint(page, stext("separate_keywords_with_commas_never_wins")),
        ]
    return card(page, stext("concurrent_downloads"), rows)


@ft.component
def DownloadPolicyCard():
    shell = use_shell()
    page = shell.page
    tick, refresh = use_refresh()
    limit = max(1, app_config.global_speed_limit_kib or 1024)

    def changed() -> None:
        sv.background_changed(shell.manager)
        refresh()

    def put(name: str, value) -> None:
        setattr(app_config, name, value)
        changed()

    def schedule(start: str | None = None, end: str | None = None) -> None:
        sv.set_schedule(start if start is not None else app_config.download_schedule_start,
                        end if end is not None else app_config.download_schedule_end)
        changed()

    return card(page, stext("download_policy"), [
        switch_row(page, stext("global_speed_limit"), app_config.global_speed_limit_enabled,
                   lambda v: put("global_speed_limit_enabled", v)),
        row(page, "KiB/s", number_field(limit, lambda t: put("global_speed_limit_kib", sv.parse_int(
            t, current=limit, low=sv.SPEED_LIMIT_RANGE[0], high=sv.SPEED_LIMIT_RANGE[1])), width=140,
            disabled=not app_config.global_speed_limit_enabled, key=f"speed:{tick}"), compact=_compact(shell)),
        switch_row(page, stext("scheduled_starts"), app_config.download_schedule_enabled,
                   lambda v: put("download_schedule_enabled", v), help=stext("only_new_tasks_start_inside_the")),
        ft.Row(spacing=Space.MD, controls=[
            text_field(app_config.download_schedule_start, lambda t: schedule(start=t), width=110, hint_text="01:00",
                       disabled=not app_config.download_schedule_enabled, key=f"start:{tick}"),
            ft.Text("—"),
            text_field(app_config.download_schedule_end, lambda t: schedule(end=t), width=110, hint_text="07:00",
                       disabled=not app_config.download_schedule_enabled, key=f"end:{tick}"),
        ]),
    ])


@ft.component
def Aria2Card():
    shell = use_shell()
    page = shell.page
    tick, refresh = use_refresh()
    testing, set_testing = ft.use_state(False)
    cfg = app_config

    def save(**changes) -> None:
        current = dict(enabled=cfg.aria2_rpc_enabled, url=cfg.aria2_rpc_url, token=cfg.aria2_rpc_token,
                       remote=cfg.aria2_remote_enabled, remote_dir=cfg.aria2_remote_dir,
                       mount_dir=cfg.aria2_remote_mount_dir, http_base=cfg.aria2_remote_http_base)
        current.update(changes)
        if not shell.caps.local_aria2:
            current["remote"] = True  # phones cannot run aria2 themselves
        sv.save_aria2(**current)
        shell.manager.apply_config()
        refresh()

    async def test() -> None:
        set_testing(True)
        try:
            data, error = await asyncio.to_thread(shell.manager._aria2_rpc_call, "aria2.getVersion", [])
        except Exception as exc:
            data, error = None, str(exc)
        set_testing(False)
        ok, title, content = sv.aria2_test_report(data, error)
        notice(page, "success" if ok else "error", title, content, duration_ms=6000)

    rows: list[ft.Control | None] = [switch_row(page, "Aria2 RPC", cfg.aria2_rpc_enabled, lambda v: save(enabled=v),
                                                help=stext("when_enabled_downloads_are_delegated_to"))]
    if not shell.caps.local_aria2:
        # Phones: the most useful way to download (8.2). The phone browses and submits; a NAS keeps the files.
        rows.append(hint(page, tr(
            "On a phone aria2 runs on another device (a NAS or PC): the phone only browses and submits, "
            "the files never pass through it, and downloads go on while the app sleeps.",
            "在手机上 aria2 运行于另一台设备（NAS 或电脑）：手机只负责浏览和提交，文件不经过手机，应用休眠时下载也不中断。",
            "スマートフォンでは aria2 を別の機器（NAS や PC）で動かします。端末は閲覧と送信だけを行い、"
            "ファイルは端末を通らず、アプリがスリープ中でもダウンロードが続きます。")))
    if cfg.aria2_rpc_enabled:
        rows += [
            text_field(cfg.aria2_rpc_url, lambda t: save(url=t), hint_text="http://127.0.0.1:6800/jsonrpc", key=f"url:{tick}"),
            text_field(cfg.aria2_rpc_token, lambda t: save(token=t), hint_text=stext("rpc_token_optional"), password=True,
                       key=f"token:{tick}"),
            switch_row(page, stext("aria2_runs_on_another_device_nas"), cfg.aria2_remote_enabled, lambda v: save(remote=v))
            if shell.caps.local_aria2 else None,
        ]
        if cfg.aria2_remote_enabled or not shell.caps.local_aria2:
            rows += [
                hint(page, stext("videos_are_saved_by_aria2_on")),
                text_field(cfg.aria2_remote_dir, lambda t: save(remote_dir=t),
                           hint_text=stext("download_folder_on_the_aria2_device"), key=f"rdir:{tick}"),
                text_field(cfg.aria2_remote_mount_dir, lambda t: save(mount_dir=t),
                           hint_text=stext("same_folder_mounted_on_this_machine"), key=f"mount:{tick}"),
                text_field(cfg.aria2_remote_http_base, lambda t: save(http_base=t),
                           hint_text=stext("http_address_serving_that_folder_for"), key=f"http:{tick}"),
            ]
        rows.append(buttons(ft.OutlinedButton(content=stext("test_connection"), icon=AppIcon.REMOTE, disabled=testing,
                                              on_click=lambda _e: page.run_task(test))))
    return card(page, "Aria2 RPC", rows)


# ── search ───────────────────────────────────────────────────────────────────


@ft.component
def SearchEngineCard():
    shell = use_shell()
    page = shell.page
    _tick, refresh = use_refresh()
    options = [("iwara", stext("iwara_live_api")), ("oreno3d", stext("oreno3d_online_search"))]

    def chinese(value: bool) -> None:
        app_config.search_chinese_via_oreno3d = value
        refresh()

    return card(page, stext("search_engine"), [
        row(page, stext("main_search_engine"), dropdown(app_config.search_default_source, options,
                                                        lambda v: (sv.set_search_source(v), refresh()), width=220),
            compact=_compact(shell)),
        switch_row(page, stext("search_chinese_keywords_with_oreno3d"), bool(app_config.search_chinese_via_oreno3d), chinese,
                   help=stext("iwara_s_own_search_rarely_matches")),
    ])


@ft.component
def SearchHistoryCard():
    shell = use_shell()
    page = shell.page
    tick, refresh = use_refresh()

    def auto(value: bool) -> None:
        app_config.search_auto_search_enabled = value
        refresh()

    return card(page, stext("search_history"), [
        row(page, stext("remembered_searches"), number_field(
            app_config.search_history_limit, lambda t: (sv.set_search_history_limit(
                sv.parse_int(t, current=app_config.search_history_limit, low=1, high=100)), refresh()),
            suffix=stext("items_1_100"), width=160, key=f"hist:{tick}"), compact=_compact(shell)),
        switch_row(page, stext("search_when_source_scope_or_sort"), app_config.search_auto_search_enabled, auto),
    ], description=stext("keep_recent_search_conditions_available_from"))


@ft.component
def SearchLimitCard():
    shell = use_shell()
    page = shell.page
    tick, refresh = use_refresh()

    def toggle(value: bool) -> None:
        app_config.search_limit_enabled = value
        refresh()

    return card(page, stext("search_download_limit"), [
        switch_row(page, stext("search_download_limit"), app_config.search_limit_enabled, toggle),
        row(page, stext("max_videos"), number_field(max(1, app_config.search_limit_count),
                                                    lambda t: (sv.set_search_limit_count(t), refresh()),
                                                    disabled=not app_config.search_limit_enabled, key=f"limit:{tick}"),
            compact=_compact(shell)),
    ])


@ft.component
def SearchBridgeCard():
    shell = use_shell()
    page = shell.page
    _tick, refresh = use_refresh()
    status, set_status = ft.use_state("")
    busy, set_busy = ft.use_state(False)
    modes = [("eager", stext("background_pre_resolve")), ("on_demand", stext("on_click_or_download"))]
    workers = [(str(n), str(n)) for n in range(sv.RESOLUTION_WORKERS_RANGE[0], sv.RESOLUTION_WORKERS_RANGE[1] + 1)]

    async def update_tags() -> None:
        set_busy(True)
        set_status(stext("updating"))
        try:
            result = await asyncio.to_thread(shell.manager.update_search_tag_dictionary)
        except Exception as exc:
            result = (0, str(exc))
        set_busy(False)
        ok, line, title, log = sv.tag_dictionary_result(result)
        set_status(line)
        _signal("log_message", log)
        notice(page, "success" if ok else "warning", title, line if ok else log.removeprefix("[Tags] "))

    return card(page, stext("oreno3d_iwara_search_loading"), [
        row(page, stext("refresh_localized_search_tags"), ft.OutlinedButton(
            content=stext("update_tags"), icon=AppIcon.SYNC, disabled=busy, on_click=lambda _e: page.run_task(update_tags)),
            help=status, compact=_compact(shell)),
        row(page, stext("resolve_timing"), dropdown(sv.resolution_mode(), modes,
                                                    lambda v: (sv.set_resolution_mode(v), refresh()), width=220),
            compact=_compact(shell)),
        row(page, stext("oreno3d_id_concurrency"), dropdown(str(sv.resolution_workers()), workers,
                                                            lambda v: (sv.set_resolution_workers(v), refresh()), width=120),
            compact=_compact(shell)),
    ])


# ── subscriptions ────────────────────────────────────────────────────────────


@ft.component
def SubscriptionAutomationCard():
    shell = use_shell()
    page = shell.page
    tick, refresh = use_refresh()
    cfg = app_config
    rules = [(str(r.get("id", "")), str(r.get("name", "") or "")) for r in rule_store.list_available()]

    def put(name: str, value) -> None:
        setattr(cfg, name, value)
        sv.background_changed(shell.manager)
        refresh()

    def refresh_now(_e) -> None:
        from ....core.background_services import background_service

        background_service.refresh_subscriptions_now()
        notice(page, "info", stext("refresh_queued"), duration_ms=2000)

    interval = cfg.subscription_refresh_interval_minutes
    return card(page, stext("subscription_automation"), [
        switch_row(page, stext("subscription_automation"), cfg.subscription_auto_refresh_enabled,
                   lambda v: put("subscription_auto_refresh_enabled", v)),
        row(page, stext("refresh_interval"), number_field(interval, lambda t: put(
            "subscription_refresh_interval_minutes", sv.parse_int(t, current=interval, low=sv.REFRESH_INTERVAL_RANGE[0],
                                                                  high=sv.REFRESH_INTERVAL_RANGE[1])),
            suffix=stext("min").strip(), key=f"interval:{tick}"), compact=_compact(shell)),
        switch_row(page, stext("desktop_notifications"), cfg.desktop_notifications_enabled,
                   lambda v: put("desktop_notifications_enabled", v)),
        switch_row(page, stext("auto_queue_rule_matches"), cfg.subscription_auto_enqueue_enabled,
                   lambda v: put("subscription_auto_enqueue_enabled", v)),
        switch_row(page, stext("auto_queued_videos_also_wait_for"), bool(cfg.subscription_auto_enqueue_confirm),
                   lambda v: put("subscription_auto_enqueue_confirm", v)),
        row(page, stext("matching_rule"), dropdown(sv.auto_enqueue_rule_id(), rules,
                                                   lambda v: put("subscription_auto_enqueue_rule_id", v), width=240),
            compact=_compact(shell)),
        buttons(ft.OutlinedButton(content=stext("refresh_now"), icon=AppIcon.SYNC, on_click=refresh_now)),
    ])


@ft.component
def SubscriptionPromptCard():
    shell = use_shell()
    page = shell.page
    _tick, refresh = use_refresh()
    options = list(zip(sv.SUBSCRIPTION_PROMPT_MODES, (stext("ask_every_time"), stext("always_add"), stext("never_ask"))))
    mode = app_config.subscription_prompt_mode if app_config.subscription_prompt_mode in sv.SUBSCRIPTION_PROMPT_MODES else "ask"

    def put(value: str) -> None:
        app_config.subscription_prompt_mode = value
        refresh()

    return card(page, stext("download_subscription_prompt"), [
        row(page, stext("download_subscription_prompt"), dropdown(mode, options, put, width=200), compact=_compact(shell)),
    ], description=stext("when_a_download_input_is_an"))


@ft.component
def CoverPerformanceCard():
    shell = use_shell()
    page = shell.page
    _tick, refresh = use_refresh()

    def numbers(low: int, high: int) -> list[tuple[str, str]]:
        return [(str(n), str(n)) for n in range(low, high + 1)]

    return card(page, stext("refresh_and_cover_performance"), [
        row(page, stext("cover_download_concurrency"), dropdown(str(sv.cover_workers()), numbers(*sv.COVER_WORKERS_RANGE),
                                                                lambda v: (sv.set_cover_workers(v), refresh()), width=120),
            compact=_compact(shell)),
        row(page, stext("subscription_refresh_concurrency"), dropdown(
            str(sv.refresh_workers()), numbers(*sv.REFRESH_WORKERS_RANGE), lambda v: (sv.set_refresh_workers(v), refresh()),
            width=120), compact=_compact(shell)),
        switch_row(page, stext("incremental_account_subscription_refresh"), sv.incremental_refresh(),
                   lambda v: (sv.set_incremental_refresh(v), refresh())),
    ])


# ── tools ────────────────────────────────────────────────────────────────────


@ft.component
def FfmpegCard():
    shell = use_shell()
    page = shell.page
    tick, refresh = use_refresh()
    report, set_report = ft.use_state(sv.ffmpeg_report(None, "", checking=True))
    palette = palette_for(page)

    async def check() -> None:
        from ....core.tools.ffmpeg import locate

        set_report(sv.ffmpeg_report(None, "", checking=True))
        try:
            info, error = await asyncio.to_thread(locate, str(app_config.ffmpeg_path or "")), ""
        except Exception as exc:  # a broken binary must not take the page down
            info, error = None, str(exc)
        set_report(sv.ffmpeg_report(info, error))

    ft.use_effect(lambda: page.run_task(check), [])

    def set_path(text: str) -> None:
        if sv.set_ffmpeg_path(text):
            page.run_task(check)
            if shell.tools.loaded:
                page.run_task(shell.tools.refresh_ffmpeg)
        refresh()

    async def browse() -> None:
        path = await _pick_file(stext("choose_ffmpeg"))
        if path:
            set_path(path)

    state, status, details = report
    color = {sv.FFMPEG_OK: palette.success.fg, sv.FFMPEG_MISSING: palette.danger.fg}.get(state)
    return card(page, "ffmpeg", [
        ft.Row(controls=[text_field(app_config.ffmpeg_path, set_path, hint_text=stext("detect_automatically"), key=f"ff:{tick}")]),
        buttons(
            ft.OutlinedButton(content=stext("browse"), icon=AppIcon.FOLDER, on_click=lambda _e: page.run_task(browse)),
            ft.OutlinedButton(content=stext("check"), icon=AppIcon.REFRESH, on_click=lambda _e: page.run_task(check)),
        ),
        ft.Text(status, style=text_style(TYPE.label, color=color)),
        hint(page, details) if details else None,
    ], description=stext("upload_compression_and_cover_preview_run"))


# ── system ───────────────────────────────────────────────────────────────────


@ft.component
def WindowCard():
    shell = use_shell()
    page = shell.page
    tick, refresh = use_refresh()
    login_on, set_login_on = ft.use_state(False)

    async def read_login() -> None:
        set_login_on(await asyncio.to_thread(login_items.is_enabled))

    def refresh_login() -> None:
        if shell.caps.window and login_items.is_supported():
            page.run_task(read_login)  # reading the registry / LaunchAgents folder stays off the loop

    ft.use_effect(refresh_login, [tick])

    def put(value: str) -> None:
        app_config.startup_page = value or "home"
        refresh()

    def keep_running(value: bool) -> None:
        app_config.minimize_to_tray = bool(value)
        tray.sync(page)
        refresh()

    async def set_login(value: bool) -> None:
        try:
            await asyncio.to_thread(login_items.set_enabled, bool(value))
        except OSError as exc:
            notice(page, "error", stext("failed"), str(exc), duration_ms=6000)
        refresh()

    startup = str(app_config.startup_page or "home")
    rows: list[ft.Control | None] = [
        row(page, stext("page_shown_at_launch"), dropdown(startup, sv.startup_pages(), put, width=200), compact=_compact(shell)),
    ]
    if shell.caps.window:
        rows += [
            switch_row(page, tray.keep_running_label(), bool(app_config.minimize_to_tray), keep_running)
            if tray.available() else None,
            switch_row(page, tr("Launch at login", "开机自动启动", "ログイン時に起動"), login_on,
                       lambda v: page.run_task(set_login, v)) if login_items.is_supported() else None,
            hint(page, tr(
                "With \"keep running\" off, closing the window asks first while downloads run.",
                "关闭“继续运行”时，若仍有下载，关闭窗口前会先询问。",
                "「動作を続ける」がオフのとき、ダウンロード中にウィンドウを閉じると確認します。",
            )),
        ]
    return card(page, stext("window_startup") if shell.caps.window else tr("Start-up", "启动", "起動"), rows)


@ft.component
def CardClicksCard():
    shell = use_shell()
    page = shell.page
    _tick, refresh = use_refresh()
    options = [(action, action_label(action)) for action in CLICK_ACTIONS]

    def put(name: str, value: str) -> None:
        setattr(app_config, name, value)
        refresh()

    return card(page, stext("clicking_results"), [
        row(page, stext("click"), dropdown(app_config.media_click_action, options, lambda v: put("media_click_action", v)),
            compact=_compact(shell)),
        row(page, stext("double_click"), dropdown(app_config.media_double_click_action, options,
                                                  lambda v: put("media_double_click_action", v)), compact=_compact(shell)),
    ], description=stext("for_the_search_page_subscriptions_and"))


@ft.component
def PlaybackCard():
    shell = use_shell()
    page = shell.page
    tick, refresh = use_refresh()

    def put(name: str, value) -> None:
        setattr(app_config, name, value)
        refresh()

    async def browse() -> None:
        path = await _pick_file(stext("choose_a_player"))
        if path:
            put("preview_player_command", sv.player_command_for(path))

    mode = app_config.preview_player_mode
    rows: list[ft.Control | None] = [
        row(page, stext("player_for_downloaded_videos"), dropdown(
            mode, [(k, t) for k, t in sv.player_modes() if shell.caps.player_command or k != video_player.MODE_CUSTOM],
            lambda v: put("preview_player_mode", v)), compact=_compact(shell)),
    ] if shell.caps.external_player else []  # phones always use the built-in player
    if mode == video_player.MODE_CUSTOM and shell.caps.player_command:
        rows += [
            ft.Row(controls=[
                text_field(app_config.preview_player_command, lambda t: put("preview_player_command", t.strip()),
                           hint_text='"mpv" --fs "{file}"', key=f"cmd:{tick}"),
                ft.OutlinedButton(content=stext("browse"), icon=AppIcon.FOLDER, on_click=lambda _e: page.run_task(browse)),
            ]),
            hint(page, stext("file_is_replaced_by_the_video")),
        ]
    rows.append(row(page, stext("streaming_quality"), dropdown(
        app_config.preview_quality, sv.quality_choices(streaming=True), lambda v: put("preview_quality", v)),
        compact=_compact(shell)))
    return card(page, stext("video_playback"), rows, description=stext("when_a_video_is_played_in"))


@ft.component
def RequestPolicyCard():
    shell = use_shell()
    page = shell.page
    tick, refresh = use_refresh()

    def put(name: str, value) -> None:
        setattr(app_config, name, value)
        refresh()

    interval, retries = max(0, app_config.request_min_interval_ms), max(0, app_config.request_max_retries)
    return card(page, stext("request_pacing"), [
        row(page, stext("minimum_interval_between_requests"), number_field(
            interval, lambda t: put("request_min_interval_ms", sv.parse_int(t, current=interval, low=0, high=5000)),
            suffix="ms", key=f"interval:{tick}"), compact=_compact(shell)),
        row(page, stext("retries_on_429_5xx_network_errors"), number_field(
            retries, lambda t: put("request_max_retries", sv.parse_int(t, current=retries, low=0, high=5)), key=f"retries:{tick}"),
            compact=_compact(shell)),
        text_field(app_config.x_version_salts, lambda t: put("x_version_salts", t.strip()),
                   label=stext("extra_x_version_salts_comma_separated"), key=f"salts:{tick}"),
    ], description=stext("spacing_and_retries_for_iwara_api"))


@ft.component
def MaintenanceCard():
    shell = use_shell()
    page = shell.page

    async def back_up() -> None:
        try:
            saved = await save_made_file(
                shell, lambda path: asyncio.to_thread(backup.create_backup, app_config.app_data_dir, path),
                dialog_title=stext("back_up_iwaratool_data"), file_name=backup.default_backup_name(), extensions=["zip"])
        except Exception as exc:
            logger.exception("Backup failed")
            notice(page, "error", stext("failed"), str(exc), duration_ms=6000)
            return
        if saved:
            path, stored = saved
            notice(page, "success", stext("done"), sv.backup_saved_text(len(stored), path), duration_ms=6000)

    async def restore() -> None:
        path = await _pick_file(stext("restore_iwaratool_data"), ["zip"])
        if not path:
            return
        if not await confirm(page, stext("restore_backup"), stext("settings_history_subscriptions_and_rules_will"),
                             informative=stext("your_current_files_are_kept_under"), confirm_label=stext("stage_restore")):
            return
        try:
            staged = await asyncio.to_thread(backup.stage_restore, path, app_config.app_data_dir)
        except Exception as exc:
            logger.exception("Restore staging failed")
            notice(page, "error", stext("failed"), str(exc), duration_ms=6000)
            return
        notice(page, "success", stext("done"), sv.restore_staged_text(len(staged)), duration_ms=6000)

    async def logs() -> None:
        directory = log_dir()
        os.makedirs(directory, exist_ok=True)
        await _open_folder(directory)

    async def save_logs() -> None:
        from ...platform.storage import export_logs

        try:
            saved = await asyncio.to_thread(export_logs, Path(app_config.download_dir or log_dir()))
        except OSError as exc:
            notice(page, "error", stext("failed"), str(exc), duration_ms=6000)
            return
        notice(page, "success", stext("done"), tr(f"Logs saved to {saved}", f"日志已保存到 {saved}", f"ログを {saved} に保存しました"),
               duration_ms=6000)

    return card(page, stext("backup_logs"), [
        buttons(
            ft.OutlinedButton(content=stext("back_up"), icon=AppIcon.SAVE, on_click=lambda _e: page.run_task(back_up)),
            ft.OutlinedButton(content=stext("restore"), icon=AppIcon.HISTORY, on_click=lambda _e: page.run_task(restore)),
            ft.OutlinedButton(content=stext("import_from_classic"), icon=AppIcon.LOGIN,
                              on_click=lambda _e: page.run_task(import_classic, page))
            if shell.caps.desktop else None,
            ft.OutlinedButton(content=stext("open_log_folder"), icon=AppIcon.FOLDER_OPEN, on_click=lambda _e: page.run_task(logs))
            if shell.caps.open_folder else
            ft.OutlinedButton(content=tr("Save logs to the download folder", "保存日志到下载文件夹", "ログをダウンロードフォルダーに保存"),
                              icon=AppIcon.SAVE, on_click=lambda _e: page.run_task(save_logs)),
        ),
    ], description=stext("backups_include_settings_history_subscriptions_r"))


# ── keyboard shortcuts ───────────────────────────────────────────────────────


@ft.component
def ShortcutsCard():
    shell = use_shell()
    page = shell.page
    _tick, refresh = use_refresh()
    recording, set_recording = ft.use_state("")
    palette = palette_for(page)
    titles = keys.scope_titles()

    def stop() -> None:
        shell.key_capture = None
        set_recording("")

    def record(action_id: str) -> None:
        def captured(chord: str, raw: str) -> None:
            folded = raw.casefold()
            if folded == "escape" and chord == "Esc":
                stop()
                return
            if folded in ("backspace", "delete") and chord in ("Backspace", "Del"):
                chord = ""  # unassign
            elif not chord:
                return  # a lone modifier: wait for the real key
            stop()
            conflict = keys.choose_key(action_id, chord)
            if conflict is not None:
                notice(page, "warning", *keys.conflict_text(conflict), duration_ms=5000)
            refresh()

        shell.key_capture = captured
        set_recording(action_id)

    ft.use_effect(lambda: None, [], cleanup=lambda: setattr(shell, "key_capture", None))

    def restore(action_id: str) -> None:
        conflict = keys.restore_key(action_id)
        if conflict is not None:
            notice(page, "warning", *keys.conflict_text(conflict), duration_ms=5000)
        refresh()

    def restore_all(_e) -> None:
        keys.restore_all()
        refresh()

    sections: list[ft.Control] = [
        hint(page, stext("click_a_key_to_record_a")),
        buttons(ft.OutlinedButton(content=stext("reset_all_to_defaults"), icon=AppIcon.SYNC, on_click=restore_all)),
    ]
    for scope in keys.SCOPE_ORDER:
        actions = [a for a in keys.all_actions() if a.scope == scope and a.id in HANDLED]
        if not actions:
            continue
        sections.append(ft.Text(titles.get(scope, scope), style=text_style(TYPE.label, color=palette.primary)))
        for action in actions:
            text = keys.native_text(keys.key_for(action.id)) or stext("unassigned")
            active = recording == action.id
            sections.append(ft.Row(spacing=Space.SM, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
                ft.Text(action.label, style=text_style(TYPE.body), expand=True),
                ft.OutlinedButton(content=stext("press_the_new_keys") if active else text, width=180,
                                  icon=AppIcon.KEYBOARD if active else None,
                                  on_click=lambda _e, a=action.id: stop() if recording == a else record(a)),
                ft.IconButton(icon=AppIcon.UNDO, tooltip=stext("restore_the_default"),
                              on_click=lambda _e, a=action.id: restore(a)),
            ]))
    return card(page, stext("keyboard_shortcuts"), sections)


CARDS = {
    "appearance": AppearanceCard,
    "font": FontCard,
    "language": LanguageCard,
    "updates": UpdatesCard,
    "data_paths": DataPathsCard,
    "home_layout": HomeLayoutCard,
    "home_cache": HomeCacheCard,
    "site": SiteCard,
    "account": AccountCard,
    "rating": RatingCard,
    "look": LookCard,
    "source": ContentSourceCard,
    "about": AboutCard,
    "proxy": ProxyCard,
    "download_dir": DownloadDirCard,
    "quality": QualityCard,
    "behavior": BehaviorCard,
    "concurrency": ConcurrencyCard,
    "download_policy": DownloadPolicyCard,
    "aria2": Aria2Card,
    "search_engine": SearchEngineCard,
    "search_history": SearchHistoryCard,
    "search_limit": SearchLimitCard,
    "search_bridge": SearchBridgeCard,
    "subscription_automation": SubscriptionAutomationCard,
    "subscription_prompt": SubscriptionPromptCard,
    "cover_performance": CoverPerformanceCard,
    "ffmpeg": FfmpegCard,
    "window": WindowCard,
    "card_clicks": CardClicksCard,
    "playback": PlaybackCard,
    "request_policy": RequestPolicyCard,
    "maintenance": MaintenanceCard,
    "shortcuts": ShortcutsCard,
}
