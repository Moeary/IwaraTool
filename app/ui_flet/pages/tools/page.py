"""Tools (docs/flet_ui_plan.md 7.10, desktop): upload compressor and cover preview.

Wide windows list the tools beside the chosen one (a single column of at most
``MAX_WIDTH_FORM``, labels beside their controls); narrow ones list the tools
and open ``/tools/<key>``.  Without a usable ffmpeg each tool shows how to get
one.  Flet cannot take files dropped from the system, so files are browsed for
or typed.  Labels and verdicts are ``presentation.tools``'s, shared with the
old page.
"""
from __future__ import annotations

from pathlib import Path

import flet as ft

from ....config import app_config
from ....core.tools.compress import AUDIO_BITRATES, X264_PRESETS
from ....core.tools.covers import format_timecode
from ....i18n import tr
from ....presentation.tools import common as tc
from ....presentation.tools import compress as cr
from ....presentation.tools import covers as cv
from ....presentation.tools.texts import tool_text
from ... import layout, routes
from ...components.page_frame import PageFrame
from ...icons import AppIcon, NavIcon
from ...stores.app_state import subscribe, use_shell
from ...stores.tools import ToolsStore
from ...theme import palette_for, text_style
from ...tokens import TYPE, Radius, Size, Space
from ..settings.rows import buttons, card, dropdown, hint, number_field, row, switch_row, use_refresh

NAV_WIDTH = 232
ICONS = {"compress": AppIcon.VIDEO, "covers": AppIcon.IMAGE}


def pane_width(view) -> float:
    """The tool column's width: beside the tool list on wide windows, the page width otherwise."""
    if view.two_pane:  # less the list, the divider and the row's two gaps
        return min(layout.MAX_WIDTH_FORM, max(320, view.body_width(layout.MAX_WIDTH_LIST_DETAIL) - NAV_WIDTH - 1 - Space.LG * 2))
    return view.body_width(layout.MAX_WIDTH_FORM)


def _role_color(palette, role: str):
    return {"success": palette.success.fg, "warning": palette.warning.fg, "danger": palette.danger.fg}.get(role, palette.on_surface)


async def _pick_video(initial: str) -> str:
    files = await ft.FilePicker().pick_files(
        dialog_title=tc.choose_video_title(), initial_directory=tc.browse_start(initial, str(app_config.download_dir or "")) or None,
        file_type=ft.FilePickerFileType.CUSTOM, allowed_extensions=[ext.lstrip(".") for ext in tc.VIDEO_EXTENSIONS])
    return str(files[0].path or "") if files else ""


async def _open_folder(path: str) -> None:
    if path:
        await ft.UrlLauncher().launch_url(Path(path).resolve().parent.as_uri())


@ft.component
def FfmpegMissing():
    shell = use_shell()
    page = shell.page
    store: ToolsStore = shell.tools
    subscribe(store.state)
    palette = palette_for(page)
    no_h264 = store.ffmpeg is not None and not store.compress_ready and store.state.tool == "compress"
    return ft.Container(padding=Space.LG, border_radius=Radius.CARD, bgcolor=palette.card,
                        border=ft.Border.all(1, palette.warning.fg), content=ft.Column(spacing=Space.MD, controls=[
        ft.Row(spacing=Space.SM, controls=[ft.Icon(AppIcon.WARNING, color=palette.warning.fg),
                                           ft.Text(tc.ffmpeg_missing_title(), style=text_style(TYPE.subtitle))]),
        ft.Text(tc.ffmpeg_missing_text(store.state.ffmpeg_error, no_h264=no_h264), style=text_style(TYPE.body), selectable=True),
        buttons(
            ft.OutlinedButton(content=tc.open_settings_text(), icon=NavIcon.SETTINGS.rest,
                              on_click=lambda _e: shell.open(routes.route(routes.SETTINGS, "tools"))),
            ft.TextButton(content=tr("Check again", "重新检测", "再確認"), icon=AppIcon.REFRESH, disabled=store.state.ffmpeg_busy,
                          on_click=lambda _e: page.run_task(store.refresh_ffmpeg)),
        ),
    ]))


def _source_row(page: ft.Page, value: str, on_load, *, disabled: bool = False, key: str = "") -> ft.Control:
    async def browse() -> None:
        path = await _pick_video(value)
        if path:
            await on_load(path)

    def commit(e) -> None:
        text = str(e.control.value or "")
        if tc.clean_path(text) != value:
            page.run_task(on_load, text)

    return ft.Row(spacing=Space.SM, controls=[
        ft.TextField(key=key, value=value, hint_text=tool_text("video_file"), dense=True, expand=True, disabled=disabled,
                     on_blur=commit, on_submit=commit),
        ft.OutlinedButton(content=tool_text("browse"), icon=AppIcon.FOLDER, disabled=disabled, on_click=lambda _e: page.run_task(browse)),
    ])


# ── upload compressor ────────────────────────────────────────────────────────


@ft.component
def CompressTool():
    shell = use_shell()
    page = shell.page
    store: ToolsStore = shell.tools
    state = store.state
    subscribe(state)
    palette = palette_for(page)
    tick, refresh = use_refresh()
    form = state.form
    compact = shell.state.layout.width_class == layout.WIDTH_COMPACT
    running = state.running
    verdict = store.verdict()

    def number(key: str, low: int, high: int):
        def commit(text: str) -> None:
            try:
                value = max(low, min(high, int(text.strip())))
            except ValueError:
                refresh()
                return
            store.set_form(**{key: value})
            refresh()
        return commit

    async def save_as() -> None:
        path = await ft.FilePicker().save_file(dialog_title=tool_text("save_as_2"), file_name=Path(state.output).name or None,
                                               initial_directory=str(Path(state.output).parent) if state.output else None,
                                               file_type=ft.FilePickerFileType.CUSTOM, allowed_extensions=["mp4"])
        if path:
            store.set_output(path if path.lower().endswith(".mp4") else path + ".mp4")

    async def preview_covers() -> None:
        store.choose_tool("covers")
        if shell.state.layout.two_pane:
            shell.open(routes.route(routes.TOOLS))
        else:
            shell.open(routes.route(routes.TOOLS, "covers"))
        await store.load_cover_source(state.last_output, force=True)

    source = card(page, tool_text("source_video"), [
        hint(page, tr("Iwara accepts H.264 + AAC in MP4, at least 720p, up to 300 MB (600 MB for premium).",
                      "Iwara 上传要求：H.264 + AAC 编码、MP4 封装，分辨率不低于 720p，大小不超过 300MB（会员 600MB）。",
                      "Iwara の条件：H.264 + AAC の MP4、720p 以上、300 MB まで（プレミアムは 600 MB）。")),
        _source_row(page, state.source, store.load_source, disabled=running, key=f"src:{state.source}"),
        ft.Text(state.media_text, style=text_style(TYPE.meta, color=palette.danger.fg if state.media_error else None),
                selectable=True) if state.media_text else None,
        ft.ProgressBar() if state.probing else None,
    ])

    verdict_card = None
    if verdict is not None:
        color = _role_color(palette, verdict.role)
        notes = list(verdict.notes) + ([cr.plan_note_text("retried")] if state.retried else [])
        verdict_card = ft.Container(padding=Space.LG, border_radius=Radius.CARD, bgcolor=palette.card, border=ft.Border.all(1, color),
                                    content=ft.Column(spacing=Space.SM, controls=[
            ft.Text(verdict.title, style=text_style(TYPE.subtitle, color=color)),
            ft.Text(verdict.detail, style=text_style(TYPE.body), selectable=True),
            *[hint(page, note) for note in notes],
        ]))

    limit_rows = [
        row(page, tool_text("size_limit"), dropdown(form.limit, cr.limit_choices(), lambda v: store.set_form(limit=v), width=220), compact=compact),
        row(page, tool_text("custom_limit"), number_field(form.custom_mb, number("custom_mb", *cr.CUSTOM_MB_RANGE), suffix="MB",
                                                           key=f"mb:{tick}:{form.custom_mb}"), compact=compact) if form.custom else None,
        row(page, tool_text("safety_margin_below_the_limit"), number_field(form.margin_pct, number("margin_pct", *cr.MARGIN_RANGE),
                                                                          suffix="%", key=f"mg:{tick}:{form.margin_pct}"), compact=compact),
    ]
    encoders = [(name, cr.encoder_label(name)) for name in cr.available_encoders(store.ffmpeg)]
    options = card(page, tool_text("compression"), [
        *limit_rows,
        row(page, tool_text("encoder"), dropdown(form.encoder, encoders, lambda v: store.set_form(encoder=v), width=300,
                                                 disabled=not encoders), compact=compact),
        row(page, tool_text("x264_speed_slower_better_quality"), dropdown(form.x264_preset, [(p, p) for p in X264_PRESETS],
                                                                          lambda v: store.set_form(x264_preset=v), width=220),
            compact=compact) if form.x264 else None,
        row(page, tool_text("aac_audio_bitrate"), dropdown(str(form.audio_kbps), [(str(r), f"{r} kbps") for r in AUDIO_BITRATES],
                                                           lambda v: store.set_form(audio_kbps=int(v)), width=220), compact=compact),
        row(page, tool_text("frame_rate"), dropdown(str(form.max_fps), [(str(v), t) for v, t in cr.fps_choices()],
                                                    lambda v: store.set_form(max_fps=float(v)), width=220), compact=compact),
        row(page, tool_text("upscaling_to_720p"), dropdown(form.upscaler, cr.upscaler_choices(), lambda v: store.set_form(upscaler=v),
                                                           width=220), compact=compact),
        switch_row(page, tool_text("lower_the_resolution_when_the"), form.auto_downscale, lambda v: store.set_form(auto_downscale=v)),
        ft.Checkbox(label=tool_text("re_encode_even_if_the"), value=form.force_encode,
                    on_change=lambda e: store.set_form(force_encode=bool(e.control.value))),
    ])

    # Output, Start and progress share one card right under the verdict; the options (set once, saved) follow.
    output = card(page, tool_text("output"), [
        ft.Row(spacing=Space.SM, controls=[
            ft.TextField(key=f"out:{state.output}", value=state.output, hint_text=tool_text("output_mp4"), dense=True, expand=True,
                         disabled=running, on_blur=lambda e: store.set_output(str(e.control.value or "")) if (e.control.value or "") != state.output else None,
                         on_submit=lambda e: store.set_output(str(e.control.value or ""))),
            ft.OutlinedButton(content=tool_text("save_as"), icon=AppIcon.SAVE, disabled=running, on_click=lambda _e: page.run_task(save_as)),
        ]),
        ft.Row(spacing=Space.SM, wrap=True, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
            ft.OutlinedButton(content=tool_text("cancel"), icon=AppIcon.CLOSE, on_click=lambda _e: store.cancel()) if running else
            ft.FilledButton(content=tool_text("start"), icon=AppIcon.PLAY, disabled=not store.can_start, on_click=lambda _e: page.run_task(store.start)),
            ft.Text(state.status, style=text_style(TYPE.meta, color=palette.on_surface_variant)),
        ]),
        ft.ProgressBar(value=state.progress) if running or state.progress > 0 else None,
        buttons(
            ft.OutlinedButton(content=tool_text("show_in_folder"), icon=AppIcon.FOLDER, on_click=lambda _e: page.run_task(_open_folder, state.last_output)),
            ft.OutlinedButton(content=tool_text("preview_covers_of_the_result"), icon=AppIcon.IMAGE,
                              on_click=lambda _e: page.run_task(preview_covers)),
        ) if state.last_output else None,
        ft.Container(padding=Space.SM, border_radius=Radius.CONTROL, bgcolor=palette.field, height=160, content=ft.ListView(controls=[
            ft.Text(state.failure_log, style=text_style(TYPE.meta, family="monospace"), selectable=True)])) if state.failure_log else None,
    ])
    missing = FfmpegMissing() if state.ffmpeg_checked and not store.compress_ready else None
    return ft.Column(spacing=Space.MD, horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                     controls=[c for c in (missing, source, verdict_card, output, options) if c])


# ── cover preview ────────────────────────────────────────────────────────────


def _frame(data: bytes | None, width: float, crop: bool, palette, message: str = "") -> ft.Control:
    height = width / cv.CARD_ASPECT if crop else width * 9 / 16
    content: ft.Control
    if data:
        content = ft.Image(src=data, width=width, height=height, fit=ft.BoxFit.COVER if crop else ft.BoxFit.CONTAIN, gapless_playback=True)
    else:
        content = ft.Text(message, style=text_style(TYPE.meta, color=palette.on_surface_variant), text_align=ft.TextAlign.CENTER)
    return ft.Container(width=width, height=height, bgcolor=palette.container, alignment=ft.Alignment.CENTER,
                        clip_behavior=ft.ClipBehavior.ANTI_ALIAS, border_radius=Radius.CONTROL, content=content)


@ft.component
def CoverTool():
    shell = use_shell()
    page = shell.page
    store: ToolsStore = shell.tools
    state = store.state
    subscribe(state)
    palette = palette_for(page)
    view = shell.state.layout
    width = pane_width(view) - Space.LG * 2
    gap = Space.SM
    columns = cv.grid_columns(width, gap)
    store.grid_columns = columns  # arrow keys move by a row
    tile_width = max(60, int((width - gap * (columns - 1)) / columns) - 1)
    crop = state.crop

    async def save() -> None:
        candidate, media = store.selected, store.cover_media
        if candidate is None or media is None:
            return
        default = cv.default_frame_path(media.path, candidate)
        path = await ft.FilePicker().save_file(dialog_title=tool_text("save_frame_2"), file_name=default.name,
                                               initial_directory=str(default.parent), file_type=ft.FilePickerFileType.CUSTOM,
                                               allowed_extensions=["png", "jpg"])
        if path:
            await store.save_frame(path)

    async def copy_time() -> None:
        candidate = store.selected
        if candidate is not None:
            await ft.Clipboard().set(format_timecode(candidate.seconds))

    source = card(page, tool_text("cover_preview"), [
        hint(page, tool_text("after_an_upload_iwara_offers")),
        _source_row(page, state.cover_source, store.load_cover_source, key=f"csrc:{state.cover_source}"),
        switch_row(page, tool_text("crop_like_a_card_3"), crop, store.set_crop),
        ft.Text(state.cover_text, style=text_style(TYPE.meta, color=palette.danger.fg if state.cover_error else None),
                selectable=True) if state.cover_text else None,
        hint(page, state.cover_status) if state.cover_status else None,
    ])

    tiles = []
    for index in range(16):
        candidate = store.candidates[index] if index < len(store.candidates) else None
        if candidate is None:
            message = "…" if state.cover_source and not state.cover_error else cv.tile_empty_text()
        elif index in store.frame_errors:
            message = cv.tile_failed_text(store.frame_errors[index])
        else:
            message = cv.tile_extracting_text()
        selected = index == state.selected
        tiles.append(ft.Container(
            width=tile_width, border_radius=Radius.CONTROL, padding=Space.XXS,
            border=ft.Border.all(2, palette.primary if selected else palette.card),
            on_click=(lambda _e, i=index: store.select(i)) if candidate else None,
            content=ft.Column(spacing=Space.XXS, horizontal_alignment=ft.CrossAxisAlignment.CENTER, controls=[
                _frame(store.frames.get(index), tile_width - Space.XXS * 2 - 4, crop, palette, message),
                ft.Text(f"{index + 1:02d} · {format_timecode(candidate.seconds)}" if candidate else f"{index + 1:02d}",
                        style=text_style(TYPE.meta, color=palette.primary if selected else palette.on_surface_variant)),
            ]),
        ))
    grid = ft.Container(padding=Space.LG, border_radius=Radius.CARD, bgcolor=palette.card,
                        content=ft.Row(wrap=True, spacing=gap, run_spacing=gap, controls=tiles))

    candidate = store.selected
    detail = card(page, cv.detail_title(candidate) if candidate else cv.preview_empty_text(), [
        _frame(store.frames.get(state.selected), width, crop, palette, cv.preview_empty_text()) if candidate else None,
        hint(page, cv.detail_text(candidate)) if candidate else None,
        buttons(
            ft.OutlinedButton(content=tool_text("save_frame"), icon=AppIcon.SAVE, disabled=state.saving or store.ffmpeg is None,
                              on_click=lambda _e: page.run_task(save)),
            ft.OutlinedButton(content=tool_text("copy_time"), icon=AppIcon.COPY, on_click=lambda _e: page.run_task(copy_time)),
        ) if candidate else None,
    ])
    missing = FfmpegMissing() if state.ffmpeg_checked and store.ffmpeg is None else None
    return ft.Column(spacing=Space.MD, horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                     controls=[c for c in (missing, source, grid, detail) if c])


TOOLS = {"compress": CompressTool, "covers": CoverTool}


# ── page ─────────────────────────────────────────────────────────────────────


@ft.component
def ToolList(selected: str, on_pick):
    shell = use_shell()
    palette = palette_for(shell.page)
    two_pane = shell.state.layout.two_pane

    def item(tool: tc.ToolInfo) -> ft.Control:
        chosen = two_pane and tool.key == selected
        return ft.Container(
            padding=Space.MD, border_radius=Radius.CONTROL, bgcolor=palette.primary_container if chosen else None,
            on_click=lambda _e, k=tool.key: on_pick(k), tooltip=tool.summary if two_pane else None,
            content=ft.Row(spacing=Space.MD, controls=[
                ft.Icon(ICONS[tool.key], size=Size.ICON_LG),
                ft.Column(spacing=Space.XXS, expand=True, controls=[
                    ft.Text(tool.title, style=text_style(TYPE.label)),
                    *([] if two_pane else [ft.Text(tool.summary, style=text_style(TYPE.meta, color=palette.on_surface_variant))]),
                ]),
            ]),
        )

    return ft.ListView(expand=True, spacing=Space.XXS, controls=[item(t) for t in tc.tools()])


@ft.component
def ToolsPage(destination: str, path: str):
    shell = use_shell()
    store: ToolsStore = shell.tools
    subscribe(store.state)
    ft.use_effect(store.load, [])
    view = shell.state.layout
    parts = list(routes.parse(path).parts)
    asked = parts[0] if parts and parts[0] in TOOLS else ""
    infos = {t.key: t for t in tc.tools()}

    def pick(key: str) -> None:
        store.choose_tool(key)
        if not view.two_pane:
            shell.open(routes.route(routes.TOOLS, key))

    if not view.two_pane:
        if not asked:
            return PageFrame(title=routes.label(routes.TOOLS), body=ToolList("", pick), max_width=layout.MAX_WIDTH_FORM)
        body = ft.ListView(expand=True, padding=ft.Padding.only(bottom=Space.XL), controls=[TOOLS[asked](key=f"tool:{asked}")])
        return PageFrame(title=infos[asked].title, body=body, max_width=layout.MAX_WIDTH_FORM)
    current = asked or store.state.tool
    palette = palette_for(shell.page)
    content_width = pane_width(view)
    body = ft.Row(expand=True, spacing=Space.LG, vertical_alignment=ft.CrossAxisAlignment.STRETCH, controls=[
        ft.Container(width=NAV_WIDTH, content=ToolList(current, pick)),
        ft.VerticalDivider(width=1),
        ft.Column(expand=True, spacing=Space.SM, controls=[
            ft.Text(infos[current].summary, style=text_style(TYPE.meta, color=palette.on_surface_variant)),
            ft.Container(expand=True, content=ft.Row(vertical_alignment=ft.CrossAxisAlignment.STRETCH, controls=[
                ft.Container(width=content_width, content=ft.ListView(
                    expand=True, padding=ft.Padding.only(bottom=Space.XL), controls=[TOOLS[current](key=f"tool:{current}")])),
            ])),
        ]),
    ])
    return PageFrame(title=routes.label(routes.TOOLS), body=body, max_width=layout.MAX_WIDTH_LIST_DETAIL)
