"""Repair Center (docs/flet_ui_plan.md 7.9, desktop): a step flow instead of one dense form.

① folder and mode → ② rule and options → ③ scan and preview (original →
target, conflicts marked and skipped) → ④ repair with progress and log →
⑤ result.  Wide windows show the steps as a horizontal bar.  Options can be
changed after a scan; the targets are re-planned.  The labels, row states
and log lines are ``presentation.repair``'s, shared with the old page.
"""
from __future__ import annotations

import os

import flet as ft

from ....core.download.template_builder import template_presets
from ....presentation import rule_editing
from ....presentation.repair import page as rp
from ....presentation.repair.texts import repair_text
from ....i18n import tr
from ... import layout, routes
from ...components.page_frame import PageFrame
from ...components.tool_tabs import tool_frame
from ...icons import AppIcon, StateIcon
from ...services.dialogs import prompt_text
from ...services.notify import notice
from ...stores.app_state import subscribe, use_shell
from ...stores.repair import STEP_DONE, STEP_FOLDER, STEP_OPTIONS, STEP_PREVIEW, STEP_RUN, RepairStore
from ...theme import palette_for, text_style
from ...tokens import TYPE, Radius, Size, Space


def step_titles() -> list[str]:
    return [
        repair_text("source_folder"),
        tr("Rule & options", "规则与选项", "ルールとオプション"),
        tr("Scan & preview", "扫描与预览", "検索とプレビュー"),
        tr("Repair", "执行修复", "修復を実行"),
        tr("Result", "结果", "結果"),
    ]


def _role_color(palette, role: str | None):
    return {
        "accent": palette.primary,
        "success": palette.success.fg,
        "warning": palette.warning.fg,
        "danger": palette.danger.fg,
        "neutral": palette.on_surface_variant,
    }.get(role or "", palette.on_surface_variant)


async def _pick_folder(title: str, initial: str) -> str:
    return str(await ft.FilePicker().get_directory_path(dialog_title=title, initial_directory=initial or None) or "")


def _card(page: ft.Page, title: str, controls: list[ft.Control | None]) -> ft.Control:
    palette = palette_for(page)
    return ft.Container(padding=Space.LG, border_radius=Radius.CARD, bgcolor=palette.card, content=ft.Column(
        spacing=Space.MD, horizontal_alignment=ft.CrossAxisAlignment.STRETCH, controls=[ft.Text(title, style=text_style(TYPE.subtitle)), *[c for c in controls if c]]))


def _hint(page: ft.Page, text: str) -> ft.Control:
    return ft.Text(text, style=text_style(TYPE.meta, color=palette_for(page).on_surface_variant))


@ft.component
def Stepper(step: int, on_pick):
    shell = use_shell()
    subscribe(shell.repair.state)
    palette = palette_for(shell.page)
    store: RepairStore = shell.repair
    # Narrow windows label only the current step.
    narrow = shell.state.layout.width_class in (layout.WIDTH_COMPACT, layout.WIDTH_MEDIUM)
    items: list[ft.Control] = []
    for index, title in enumerate(step_titles()):
        current, past = index == step, index < step
        reachable = not store.state.busy and (index <= STEP_OPTIONS or (index == STEP_PREVIEW and store.items)
                                              or (index == STEP_DONE and store.state.result))
        dot = ft.Container(
            width=24, height=24, border_radius=Radius.FULL, alignment=ft.Alignment.CENTER,
            bgcolor=palette.primary if current or past else palette.container,
            content=ft.Icon(AppIcon.CHECK, size=Size.ICON_SM, color=palette.on_primary) if past else ft.Text(
                str(index + 1), style=text_style(TYPE.meta, color=palette.on_primary if current else palette.on_surface_variant)),
        )
        items.append(ft.Container(
            on_click=(lambda _e, i=index: on_pick(i)) if reachable else None, border_radius=Radius.CONTROL,
            padding=ft.Padding.symmetric(horizontal=Space.SM, vertical=Space.XS),
            tooltip=title if narrow and not current else None,
            content=ft.Row(tight=True, spacing=Space.SM, controls=[
                dot, *([ft.Text(title, style=text_style(TYPE.label, color=None if current else palette.on_surface_variant))]
                       if current or not narrow else [])]),
        ))
        if index < 4:
            items.append(ft.Container(width=Space.LG if narrow else 24, height=1, bgcolor=palette.outline_variant))
    return ft.Row(scroll=ft.ScrollMode.AUTO, spacing=Space.XS, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=items)


@ft.component
def FolderStep():
    shell = use_shell()
    subscribe(shell.repair.state)
    page = shell.page
    store: RepairStore = shell.repair
    form = store.state.form

    async def browse(output: bool) -> None:
        title = repair_text("choose_output_folder" if output else "choose_source_folder")
        path = await _pick_folder(title, form.output if output else form.folder)
        if path:
            store.update(**({"output": path} if output else {"folder": path}))

    organize = form.move_to_output
    return _card(page, repair_text("source_folder"), [
        _hint(page, tr("Choose the folder to repair; its subfolders are scanned too.", "选择要修复的文件夹；子文件夹也会扫描。",
                       "修復するフォルダーを選択します。サブフォルダーも検索します。")),
        ft.Row(controls=[
            ft.TextField(value=form.folder, hint_text=tr("Folder path", "文件夹路径", "フォルダーのパス"), dense=True, expand=True,
                         on_change=lambda e: store.update(folder=e.control.value or "")),
            ft.OutlinedButton(content=repair_text("browse"), icon=AppIcon.FOLDER, on_click=lambda _e: page.run_task(browse, False)),
        ]),
        ft.SegmentedButton(selected=[form.mode], show_selected_icon=False,
                           on_change=lambda e: store.update(mode=(list(e.control.selected or []) or [form.mode])[0]),
                           segments=[ft.Segment(value=k, label=v) for k, v in rp.mode_choices()]),
        _hint(page, rp.mode_hint(form.mode)),
        ft.Row(controls=[
            ft.TextField(value=form.output, label=repair_text("output"), hint_text=repair_text("leave_blank_to_organize_inside"),
                         dense=True, expand=True, on_change=lambda e: store.update(output=e.control.value or "")),
            ft.OutlinedButton(content=repair_text("browse"), icon=AppIcon.FOLDER, on_click=lambda _e: page.run_task(browse, True)),
        ]) if organize else None,
    ])


@ft.component
def OptionsStep():
    shell = use_shell()
    subscribe(shell.repair.state)
    page = shell.page
    store: RepairStore = shell.repair
    palette = palette_for(page)
    form = store.state.form
    template = form.effective_template
    valid = rule_editing.template_validity(template)[0]

    def check(key: str, label: str) -> ft.Control:
        return ft.Checkbox(label=label, value=bool(getattr(form, key)),
                           on_change=lambda e, key=key: store.update(**{key: bool(e.control.value)}))

    return ft.Column(spacing=Space.MD, controls=[
        _card(page, repair_text("repair_rule"), [
            _hint(page, repair_text("in_place_mode_uses_only")),
            ft.Row(wrap=False, spacing=Space.SM, controls=[
                ft.Dropdown(label=repair_text("rule"), value=store.state.rule_id or None, dense=True, expand=True,
                            options=[ft.DropdownOption(key=k, text=t) for k, t in store.rule_choices()],
                            on_select=lambda e: store.choose_rule(str(e.control.value or ""))),
                ft.PopupMenuButton(
                    tooltip=repair_text("preset"), icon=AppIcon.LIST_VIEW,
                    items=[ft.PopupMenuItem(content=f"{p.label}  —  {p.template}", on_click=lambda _e, t=p.template: store.update(template=t))
                           for p in template_presets()]),
            ]),
            ft.TextField(label=repair_text("name"), value=form.template, hint_text=rp.DEFAULT_TEMPLATE, dense=True,
                         on_change=lambda e: store.update(template=e.control.value or "")),
            ft.Text(rule_editing.template_preview_text(template),
                    style=text_style(TYPE.meta, color=None if valid else palette.danger.fg), selectable=True),
        ]),
        _card(page, repair_text("repair_options"), [
            check("rename", repair_text("rename_existing_videos")),
            check("download_video", repair_text("allow_video_download_when_needed")),
            _hint(page, repair_text("existing_local_videos_are_always")),
            check("download_thumbnail", repair_text("download_cover_screenshot")),
            check("collect_nfo", repair_text("write_nfo_metadata")),
            check("add_to_history", repair_text("add_to_download_history")),
        ]),
    ])


def _row(page: ft.Page, store: RepairStore, item: dict, styles: dict, compact: bool) -> ft.Control:
    palette = palette_for(page)
    key = rp.state_key(item)
    label, role = styles.get(key, (key or styles[""][0], None))
    color = _role_color(palette, role)
    path = str(item.get("path", "") or "")
    target = str(item.get("target_path", "") or "")
    title = str(item.get("title", "") or "")
    vid = str(item.get("video_id", "") or "")
    detail = rp.row_detail(item, hint=tr("enter its Iwara ID with ✎", "可用 ✎ 填写 Iwara ID", "✎ で Iwara ID を入力できます"))

    async def enter_id() -> None:
        text = await prompt_text(page, tr("Look up by Iwara ID", "按 Iwara ID 查询", "Iwara ID で検索"), "Iwara ID / URL",
                                 confirm_label=tr("Look up", "查询", "検索"), value=vid)
        if text is None or text.strip() == vid:
            return
        if not await store.resolve(path, text):
            notice(page, "warning", *rp.warning("invalid_id"))

    badge = ft.Container(width=72, padding=ft.Padding.symmetric(vertical=Space.XXS), border_radius=Radius.BADGE,
                         border=ft.Border.all(1, color), alignment=ft.Alignment.CENTER,
                         content=ft.Text(label, style=text_style(TYPE.meta, color=color)))
    lines: list[ft.Control] = [
        ft.Text(" · ".join(p for p in (vid, title, str(item.get("published_at", "") or "")[:10]) if p) or os.path.basename(path),
                style=text_style(TYPE.label), max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
        ft.Text(path, style=text_style(TYPE.meta, color=palette.on_surface_variant), max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
                tooltip=path),
    ]
    if target and target != path:
        lines.append(ft.Text(f"→ {target}", style=text_style(TYPE.meta, color=palette.primary), max_lines=1,
                             overflow=ft.TextOverflow.ELLIPSIS, tooltip=target))
    if detail:
        lines.append(ft.Text(detail, style=text_style(TYPE.meta, color=color if role in ("danger", "warning") else palette.on_surface_variant),
                             max_lines=2 if compact else 1, overflow=ft.TextOverflow.ELLIPSIS, tooltip=detail))
    edit = ft.IconButton(icon=AppIcon.EDIT, tooltip=rp.manual_id_hint(), disabled=bool(store.state.busy),
                         on_click=lambda _e: page.run_task(enter_id)) if rp.id_editable(item) else ft.Container(width=Size.TOUCH_TARGET)
    if compact:
        # Phones: the state badge and the edit button share the top line, the text gets the full width.
        content = ft.Column(spacing=Space.XXS, controls=[
            ft.Row(alignment=ft.MainAxisAlignment.SPACE_BETWEEN, controls=[badge, edit]), *lines])
    else:
        content = ft.Row(spacing=Space.MD, vertical_alignment=ft.CrossAxisAlignment.START, controls=[
            badge, ft.Column(spacing=Space.XXS, expand=True, controls=lines), edit])
    return ft.Container(padding=ft.Padding.symmetric(horizontal=Space.MD, vertical=Space.SM), border_radius=Radius.CONTROL,
                        bgcolor=palette.card, content=content)


@ft.component
def ItemList():
    shell = use_shell()
    subscribe(shell.repair.state)
    page = shell.page
    store: RepairStore = shell.repair
    state = store.state
    palette = palette_for(page)
    compact = shell.state.layout.width_class == layout.WIDTH_COMPACT
    styles = rp.state_styles()
    chips = [ft.Container(padding=ft.Padding.symmetric(horizontal=Space.SM, vertical=Space.XXS), border_radius=Radius.FULL,
                          bgcolor=palette.container, content=ft.Text(f"{label} {count}", style=text_style(TYPE.meta)))
             for label, count in rp.summary_counts(store.items)]
    rows = [_row(page, store, item, styles, compact) for item in store.items]
    empty = ft.Container(padding=Space.XL, alignment=ft.Alignment.CENTER, content=ft.Text(
        repair_text("no_folder_scanned") if not state.busy else state.status, style=text_style(TYPE.meta, color=palette.on_surface_variant)))
    return ft.Column(expand=True, spacing=Space.SM, controls=[
        ft.Row(wrap=True, spacing=Space.SM, run_spacing=Space.SM, controls=chips),
        ft.ListView(expand=True, spacing=Space.XS, controls=rows) if rows else empty,
    ])


@ft.component
def LogPanel(*, expand: bool = False):
    shell = use_shell()
    subscribe(shell.repair.state)
    page = shell.page
    store: RepairStore = shell.repair
    palette = palette_for(page)
    open_, set_open = ft.use_state(expand)
    lines = store.log[-400:]
    head = ft.Row(controls=[
        ft.Container(expand=True, on_click=lambda _e: set_open(not open_), content=ft.Row(spacing=Space.SM, controls=[
            ft.Icon(StateIcon.EXPAND.pick(open_), size=Size.ICON),
            ft.Text(repair_text("repair_log"), style=text_style(TYPE.label)),
            ft.Text(str(len(store.log)), style=text_style(TYPE.meta, color=palette.on_surface_variant)),
        ])),
        ft.IconButton(icon=AppIcon.DELETE, tooltip=repair_text("clear_log"), on_click=lambda _e: store.clear_log()),
    ])
    body = ft.Container(
        height=None if expand else 220, expand=expand, padding=Space.SM, border_radius=Radius.CONTROL, bgcolor=palette.field,
        content=ft.ListView(auto_scroll=True, controls=[
            ft.Text(line, style=text_style(TYPE.meta, family="monospace"), selectable=True) for line in lines
        ] or [ft.Text(repair_text("repair_logs_will_appear_here"), style=text_style(TYPE.meta, color=palette.on_surface_variant))]),
    )
    return ft.Column(expand=expand and open_, spacing=Space.XS, controls=[head, *([body] if open_ else [])])


@ft.component
def RepairPage(destination: str, path: str):
    shell = use_shell()
    page = shell.page
    store: RepairStore = shell.repair
    state = store.state
    subscribe(state)
    palette = palette_for(page)
    ft.use_effect(store.load, [])
    step = state.step
    busy = bool(state.busy)
    compact = shell.state.layout.width_class == layout.WIDTH_COMPACT

    async def scan() -> None:
        if not await store.scan():
            notice(page, "warning", *rp.warning("folder"))

    async def repair() -> None:
        if not await store.repair():
            notice(page, "warning", *rp.warning("empty"))

    progress = None
    if busy and state.busy in ("scan", "repair"):
        value = state.done / state.total if state.total else None
        progress = ft.Column(spacing=Space.XS, controls=[
            ft.ProgressBar(value=value),
            ft.Row(controls=[ft.Text(state.status, style=text_style(TYPE.meta), expand=True),
                             ft.TextButton(content=repair_text("stop"), icon=AppIcon.STOP, on_click=lambda _e: store.stop())]),
        ])

    if step == STEP_FOLDER:
        content = ft.ListView(expand=True, controls=[FolderStep()])
        actions = [ft.FilledButton(content=tr("Next", "下一步", "次へ"), icon=AppIcon.FORWARD,
                                   disabled=not state.form.folder.strip(), on_click=lambda _e: store.go(STEP_OPTIONS))]
    elif step == STEP_OPTIONS:
        content = ft.ListView(expand=True, controls=[OptionsStep()])
        actions = [ft.TextButton(content=tr("Back", "上一步", "戻る"), on_click=lambda _e: store.go(STEP_FOLDER)),
                   ft.FilledButton(content=repair_text("scan"), icon=AppIcon.SEARCH, on_click=lambda _e: page.run_task(scan))]
    elif step == STEP_PREVIEW:
        content = ft.Column(expand=True, spacing=Space.SM, controls=[c for c in (progress, ItemList(), LogPanel()) if c])
        actions = [
            ft.TextButton(content=tr("Options", "调整选项", "オプション"), disabled=busy, on_click=lambda _e: store.go(STEP_OPTIONS)),
            ft.OutlinedButton(content=repair_text("scan"), icon=AppIcon.REFRESH, disabled=busy, on_click=lambda _e: page.run_task(scan)),
            ft.FilledButton(content=tr("Repair", "修复", "修復") if compact else repair_text("repair_selected_folder"), icon=AppIcon.PLAY,
                            disabled=busy or not rp.repairable(store.items), on_click=lambda _e: page.run_task(repair)),
        ]
    elif step == STEP_RUN:
        content = ft.Column(expand=True, spacing=Space.SM, controls=[c for c in (progress, LogPanel(expand=True), ItemList()) if c])
        actions = []
    else:
        result = state.result
        numbers = [(tr("Renamed", "重命名", "名前変更"), result.get("renamed", 0)), ("NFO", result.get("nfo", 0)),
                   (tr("Covers", "封面", "サムネイル"), result.get("thumbnail", 0)),
                   (tr("Skipped", "跳过", "スキップ"), result.get("skipped", 0)), (tr("Failed", "失败", "失敗"), result.get("failed", 0))]
        tiles = ft.Row(wrap=True, spacing=Space.MD, run_spacing=Space.MD, controls=[
            ft.Container(width=140, padding=Space.MD, border_radius=Radius.CARD, bgcolor=palette.card, content=ft.Column(spacing=0, controls=[
                ft.Text(str(value), style=text_style(TYPE.section_title)), ft.Text(label, style=text_style(TYPE.meta))]))
            for label, value in numbers])
        content = ft.Column(expand=True, spacing=Space.MD, controls=[tiles, ItemList(), LogPanel()])
        actions = [ft.OutlinedButton(content=tr("Start over", "重新开始", "最初から"), icon=AppIcon.RETRY,
                                     on_click=lambda _e: store.go(STEP_FOLDER))]

    bar = ft.Container(padding=ft.Padding.only(top=Space.SM), border=ft.Border.only(top=ft.BorderSide(1, palette.outline_variant)),
                       content=ft.Row(alignment=ft.MainAxisAlignment.END, wrap=True, spacing=Space.SM, run_spacing=Space.SM,
                                      controls=actions)) if actions else None
    body = ft.Column(expand=True, spacing=Space.MD, horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                     controls=[c for c in (Stepper(step, store.go), content, bar) if c])
    width = layout.MAX_WIDTH_FORM if step in (STEP_FOLDER, STEP_OPTIONS) else layout.MAX_WIDTH_LIST_DETAIL
    return PageFrame(body=body, max_width=width, **tool_frame(shell, routes.REPAIR, repair_text("repair_center")))
