"""Download rules.

Wide windows: the rule list on the left (● marks the default, "used by"
lists the pages), the editor on the right.  Phones: the list, and a rule
opens ``/rules/<id>``.  The editor leads with the sample file name; the
template can be typed, picked from presets or extended field by field.
"Rules per page" is a folding card above the list.
"""
from __future__ import annotations

import flet as ft

from ....core.download.template_builder import template_fields, template_presets
from ....core.rules import PAGE_RULE_SCOPES, page_rule_choice
from ....i18n import tr
from ....presentation import rule_editing as rules
from ....presentation.search.tag_completion import suggestion_tooltip
from ....presentation.rules import DRAFT_RULE_ID, page_scope_name, rule_choices
from ... import layout, routes
from ...components.page_frame import PageFrame
from ...components.tools_hub import hub_frame
from ...icons import AppIcon, NavIcon, StateIcon
from ...services.dialogs import confirm
from ...services.notify import notice
from ...stores.app_state import subscribe, use_shell
from ...stores.rules import RulesStore
from ...theme import palette_for, text_style
from ...tokens import TYPE, Radius, Size, Space

LIST_PANE = 320


async def _leave_ok(shell) -> bool:
    """Ask before dropping an edited rule."""
    store: RulesStore = shell.rules
    if not store.state.dirty or store.state.selected == DRAFT_RULE_ID and not store.draft:
        return True
    title, message, discard = rules.unsaved_question()
    return await confirm(shell.page, title, message, confirm_label=discard, danger=True)


async def choose(shell, rule_id: str) -> None:
    store: RulesStore = shell.rules
    if rule_id == store.state.selected:
        if not shell.state.layout.two_pane:
            shell.open(routes.route(routes.RULES, rule_id))
        return
    if store.state.dirty and not await _leave_ok(shell):
        return
    if store.draft is not None and rule_id != DRAFT_RULE_ID:
        store.draft = None
        store.load(rule_id)
    else:
        store.select(rule_id)
    if not shell.state.layout.two_pane:
        shell.open(routes.route(routes.RULES, rule_id))


# ── page bindings ────────────────────────────────────────────────────────────


@ft.component
def PageRulePickers():
    """One picker per page: which rule that page downloads with."""
    shell = use_shell()
    store: RulesStore = shell.rules
    subscribe(store.state)
    palette = palette_for(shell.page)
    saved = [r for r in store.rules if r["id"] != DRAFT_RULE_ID]
    choices = rule_choices(saved, store.active_id)

    def picked(scope: str, value: str) -> None:
        rules.choose_page_rule(scope, value)
        store.refresh()

    return ft.Column(tight=True, spacing=Space.MD, controls=[
        ft.Text(rules.page_rules_hint(), style=text_style(TYPE.meta, color=palette.on_surface_variant)),
        *[ft.Dropdown(
            label=page_scope_name(scope), value=page_rule_choice(scope), dense=True, expand=True,
            options=[ft.DropdownOption(key=key, text=text) for key, text in choices],
            on_select=lambda e, scope=scope: picked(scope, e.control.value),
        ) for scope in PAGE_RULE_SCOPES],
    ])


def open_page_rules(shell) -> None:
    page = shell.page
    page.show_dialog(ft.AlertDialog(
        title=ft.Text(rules.page_rules_title()),
        content=ft.Container(width=Size.DIALOG_MAX_WIDTH * 0.8, content=PageRulePickers()),
        actions=[ft.TextButton(content=tr("Done", "完成", "完了"), on_click=lambda _e: page.pop_dialog())],
    ))


@ft.component
def RuleWorkspace():
    """Wide windows, beside the tools list: pick a rule at the top, edit it below."""
    shell = use_shell()
    store: RulesStore = shell.rules
    state = store.state
    subscribe(state)
    palette = palette_for(shell.page)
    buttons = rules.button_texts()
    current = next((r for r in store.rules if r["id"] == state.selected), None)

    def new(_e) -> None:
        async def go():
            if await _leave_ok(shell):
                store.new()
        shell.page.run_task(go)

    picker = ft.Dropdown(
        value=state.selected or None, dense=True, width=300, leading_icon=NavIcon.RULES.rest,
        options=[ft.DropdownOption(key=r["id"], text=rules.rule_lines(r, store.active_id)[0]) for r in store.rules],
        on_select=lambda e: shell.page.run_task(choose, shell, e.control.value),
    )
    head = ft.Row(spacing=Space.SM, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
        picker,
        ft.IconButton(icon=AppIcon.ADD, tooltip=buttons["new"], on_click=new),
        ft.Container(expand=True),
        ft.TextButton(content=rules.page_rules_title(), icon=AppIcon.LIST_VIEW, on_click=lambda _e: open_page_rules(shell)),
    ])
    lines: list[ft.Control] = []
    if current is not None:
        _name, kind, used = rules.rule_lines(current, store.active_id)
        lines.append(ft.Text("  ·  ".join(t for t in (kind, used) if t), max_lines=2,
                             style=text_style(TYPE.meta, color=palette.on_surface_variant)))
    return ft.Column(expand=True, spacing=Space.SM, controls=[
        head, *lines, ft.Container(expand=True, content=RuleEditor(key=f"editor:{state.selected}")),
    ])


@ft.component
def PageBindings():
    shell = use_shell()
    store: RulesStore = shell.rules
    subscribe(store.state)
    palette = palette_for(shell.page)
    open_, set_open = ft.use_state(False)
    saved = [r for r in store.rules if r["id"] != DRAFT_RULE_ID]
    choices = rule_choices(saved, store.active_id)

    def picked(scope: str, value: str) -> None:
        rules.choose_page_rule(scope, value)
        store.refresh()

    pickers = [
        ft.Dropdown(
            label=page_scope_name(scope), value=page_rule_choice(scope), dense=True, width=220,
            options=[ft.DropdownOption(key=key, text=text) for key, text in choices],
            on_select=lambda e, scope=scope: picked(scope, e.control.value),
        )
        for scope in PAGE_RULE_SCOPES
    ]
    head = ft.Container(
        on_click=lambda _e: set_open(not open_),
        content=ft.Row(spacing=Space.SM, controls=[
            ft.Icon(StateIcon.EXPAND.active if open_ else StateIcon.EXPAND.rest, size=Size.ICON),
            ft.Text(rules.page_rules_title(), style=text_style(TYPE.label)),
        ]),
    )
    controls: list[ft.Control] = [head]
    if open_:
        controls += [
            ft.Text(rules.page_rules_hint(), style=text_style(TYPE.meta, color=palette.on_surface_variant)),
            ft.Row(wrap=True, spacing=Space.MD, run_spacing=Space.MD, controls=pickers),
        ]
    return ft.Container(padding=Space.MD, border_radius=Radius.CARD, bgcolor=palette.card,
                        content=ft.Column(spacing=Space.SM, controls=controls))


# ── the list ─────────────────────────────────────────────────────────────────


@ft.component
def RuleList(*, expand: bool = True):
    shell = use_shell()
    store: RulesStore = shell.rules
    subscribe(store.state)
    palette = palette_for(shell.page)
    buttons = rules.button_texts()
    two_pane = shell.state.layout.two_pane

    def row(rule: dict) -> ft.Control:
        name, kind, used = rules.rule_lines(rule, store.active_id)
        chosen = two_pane and rule["id"] == store.state.selected
        draft = rule["id"] == DRAFT_RULE_ID
        default = rule["id"] == store.active_id
        return ft.Container(
            padding=Space.MD, border_radius=Radius.CARD,
            bgcolor=palette.primary_container if chosen else (palette.warning.bg if draft else palette.card),
            border=ft.Border.all(1, palette.primary) if default and not chosen else None,
            on_click=lambda _e, rule_id=rule["id"]: shell.page.run_task(choose, shell, rule_id),
            tooltip=str(rule["payload"].get("filename_template", "")),
            content=ft.Column(spacing=Space.XXS, controls=[
                ft.Text(name, style=text_style(TYPE.label), max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                ft.Text(kind, style=text_style(TYPE.meta, color=palette.on_surface_variant), max_lines=2),
                *([ft.Text(used, style=text_style(TYPE.meta, color=palette.primary), max_lines=2)] if used else []),
            ]),
        )

    def new(_e) -> None:
        async def go():
            if await _leave_ok(shell):
                store.new()
                if not two_pane:
                    shell.open(routes.route(routes.RULES, DRAFT_RULE_ID))
        shell.page.run_task(go)

    return ft.Column(expand=expand, spacing=Space.SM, controls=[
        ft.ListView(expand=True, spacing=Space.SM, controls=[row(r) for r in store.rules]),
        ft.Row(spacing=Space.SM, controls=[
            ft.FilledButton(content=buttons["new"], icon=AppIcon.ADD, on_click=new),
        ]),
    ])


# ── the editor ───────────────────────────────────────────────────────────────


def _section(page: ft.Page, title: str, controls: list[ft.Control]) -> ft.Control:
    palette = palette_for(page)
    return ft.Container(padding=Space.MD, border_radius=Radius.CARD, bgcolor=palette.card, content=ft.Column(
        spacing=Space.SM, controls=[ft.Text(title, style=text_style(TYPE.subtitle)), *controls]))


@ft.component
def RuleEditor():
    shell = use_shell()
    store: RulesStore = shell.rules
    state = store.state
    subscribe(state)
    page = shell.page
    palette = palette_for(page)
    labels = rules.form_labels()
    buttons = rules.button_texts()
    form = state.form
    builtin = store.builtin
    compact = shell.state.layout.width_class == layout.WIDTH_COMPACT
    if not state.selected or not form:
        return ft.Container(expand=True)

    def text(key: str, label: str, *, hint: str = "", width: float | None = None, digits: bool = False,
             disabled: bool = False) -> ft.Control:
        return ft.TextField(
            label=label, value=str(form.get(key, "")), hint_text=hint or None, dense=True, width=width,
            expand=width is None, disabled=disabled,
            keyboard_type=ft.KeyboardType.NUMBER if digits else None,
            on_change=lambda e, key=key: store.set_field(key, e.control.value or ""),
        )

    def switch(key: str, label: str) -> ft.Control:
        return ft.Switch(label=label, value=bool(form.get(key)), on_change=lambda e, key=key: store.set_field(key, bool(e.control.value)))

    def insert(token: str) -> None:
        template = str(form.get("filename_template", "") or "")
        stem, ext = (template[:-4], template[-4:]) if template.lower().endswith(".mp4") else (template, ".mp4")
        joiner = "" if not stem or stem.endswith(("/", "_", "-", " ", "[", "(")) else "_"
        store.set_field("filename_template", f"{stem}{joiner}{{{token}}}{ext}")

    def validate(_e) -> None:
        valid, title, message = rules.template_validity(str(form.get("filename_template", "")))
        notice(page, "success" if valid else "error", title, message)

    template = str(form.get("filename_template", "") or "")
    preview = rules.template_preview_text(template)
    valid = rules.template_validity(template)[0]
    storage = _section(page, labels["storage"], [
        ft.Container(
            padding=Space.MD, border_radius=Radius.CONTROL, bgcolor=palette.field,
            content=ft.Text(preview, style=text_style(TYPE.body, color=None if valid else palette.danger.fg), selectable=True),
        ),
        ft.Row(controls=[text("filename_template", labels["template"])]),
        ft.Row(wrap=True, spacing=Space.SM, run_spacing=Space.SM, controls=[
            ft.PopupMenuButton(
                content=ft.Container(padding=Space.SM, content=ft.Row(tight=True, spacing=Space.XS, controls=[
                    ft.Icon(AppIcon.LIST_VIEW, size=Size.ICON), ft.Text(labels["presets"], style=text_style(TYPE.label))])),
                items=[ft.PopupMenuItem(content=f"{p.label}  —  {p.template}",
                                        on_click=lambda _e, t=p.template: store.set_field("filename_template", t))
                       for p in template_presets()],
            ),
            ft.PopupMenuButton(
                content=ft.Container(padding=Space.SM, content=ft.Row(tight=True, spacing=Space.XS, controls=[
                    ft.Icon(AppIcon.ADD, size=Size.ICON), ft.Text(labels["insert_field"], style=text_style(TYPE.label))])),
                items=[ft.PopupMenuItem(content=f"{f.label}  {{{f.token}}}", on_click=lambda _e, t=f.token: insert(t))
                       for f in template_fields()],
            ),
            ft.OutlinedButton(content=labels["validate"], icon=AppIcon.CHECK, tooltip=labels["validate_tip"], on_click=validate),
        ]),
        ft.Text(labels["template_help"], style=text_style(TYPE.meta, color=palette.on_surface_variant)),
    ])
    def suggestions(key: str) -> list[ft.Control]:
        """Tag candidates under the field being typed in (same dictionary as the search bar)."""
        if state.tag_field != key or not state.suggestions:
            return []
        return [ft.Row(spacing=Space.XS, scroll=ft.ScrollMode.AUTO, controls=[
            ft.Chip(label=item.display_text, tooltip=suggestion_tooltip(item),
                    on_click=lambda _e, k=item.key: store.apply_suggestion(k))
            for item in state.suggestions
        ])]

    filters_on = bool(form.get("filter_enabled"))
    width = None if compact else 200
    filters = _section(page, labels["filters"], [
        switch("filter_enabled", labels["filter_enabled"]),
        ft.Row(spacing=Space.MD, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
            switch("filter_min_likes_enabled", labels["likes"]),
            text("filter_min_likes", labels["likes"], width=width, digits=True, disabled=not filters_on)]),
        ft.Row(spacing=Space.MD, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
            switch("filter_min_views_enabled", labels["views"]),
            text("filter_min_views", labels["views"], width=width, digits=True, disabled=not filters_on)]),
        switch("filter_date_enabled", labels["dates"]),
        ft.Row(spacing=Space.MD, controls=[text("filter_start_date", labels["start"], disabled=not filters_on),
                                           text("filter_end_date", labels["end"], disabled=not filters_on)]),
        switch("filter_include_tags_enabled", labels["include_tags"]),
        ft.Row(controls=[text("filter_include_tags", labels["include_tags"], hint="2d,mmd", disabled=not filters_on)]),
        *suggestions("filter_include_tags"),
        switch("filter_exclude_tags_enabled", labels["exclude_tags"]),
        ft.Row(controls=[text("filter_exclude_tags", labels["exclude_tags"], hint="vr,ai", disabled=not filters_on)]),
        *suggestions("filter_exclude_tags"),
        ft.Text(labels["tags_hint"], style=text_style(TYPE.meta, color=palette.on_surface_variant)),
    ])
    titles = _section(page, labels["titles"], [
        ft.Row(controls=[text("title_include", labels["title_include"], hint=labels["keywords_hint"])]),
        ft.Row(controls=[text("title_exclude", labels["title_exclude"], hint=labels["keywords_hint"])]),
    ])
    behavior = _section(page, labels["behavior"], [
        switch("download_video_file", labels["download_video"]),
        switch("mark_submitted_as_downloaded", labels["mark_only"]),
        switch("download_thumbnail", labels["download_thumbnail"]),
        switch("collect_nfo_info", labels["collect_nfo"]),
        switch("record_to_history", labels["record_history"]),
        ft.Text(rules.history_hint(bool(form.get("record_to_history"))), style=text_style(TYPE.meta, color=palette.on_surface_variant)),
    ])
    basic = _section(page, labels["name"], [
        ft.Row(controls=[ft.TextField(value=state.name, hint_text=labels["name_hint"], dense=True, expand=True, read_only=builtin,
                                      on_change=lambda e: store.set_name(e.control.value or ""))]),
        ft.Text(f"{labels['summary']}: {store.summary()}", style=text_style(TYPE.meta, color=palette.on_surface_variant)),
    ])

    def save(_e) -> None:
        rule = store.save()
        if rule:
            notice(page, "success", rules.saved_title(), rule.get("name", ""))
        else:
            notice(page, "error", rules.invalid_title(), state.error)

    def make_default(_e) -> None:
        if state.selected == DRAFT_RULE_ID:
            notice(page, "warning", *rules.save_first_notice())
        elif store.make_default():
            notice(page, "success", rules.default_updated_title(), state.name)
        else:
            notice(page, "error", rules.invalid_title(), state.error)

    async def delete() -> None:
        if state.selected != DRAFT_RULE_ID:
            title, question = rules.delete_confirmation()
            if not await confirm(page, title, question, confirm_label=buttons["delete"], danger=True):
                return
        if store.delete() and not shell.state.layout.two_pane:
            shell.back()

    def duplicate(_e) -> None:
        store.duplicate()
        if state.error:
            notice(page, "error", rules.invalid_title(), state.error)

    bar = ft.Container(
        padding=ft.Padding.symmetric(vertical=Space.SM),
        border=ft.Border.only(top=ft.BorderSide(1, palette.outline_variant)),
        content=ft.Row(wrap=True, spacing=Space.SM, run_spacing=Space.SM, alignment=ft.MainAxisAlignment.END, controls=[
            *([ft.TextButton(content=buttons["delete"], icon=AppIcon.DELETE, on_click=lambda _e: page.run_task(delete))]
              if not builtin else []),
            ft.TextButton(content=buttons["duplicate"], icon=AppIcon.COPY, on_click=duplicate),
            ft.OutlinedButton(content=buttons["default"], icon=AppIcon.CHECK,
                              disabled=state.selected == store.active_id and not state.dirty, on_click=make_default),
            ft.FilledButton(content=buttons["save"], icon=AppIcon.SAVE, disabled=builtin or not state.dirty, on_click=save),
        ]),
    )
    return ft.Column(expand=True, spacing=Space.SM, controls=[
        ft.ListView(expand=True, spacing=Space.MD, controls=[basic, storage, filters, titles, behavior]),
        bar,
    ])


# ── the page ─────────────────────────────────────────────────────────────────


def _connect(shell) -> None:
    store: RulesStore = shell.rules
    if not store.loaded:
        store.load()


@ft.component
def RulesOverview():
    shell = use_shell()
    store: RulesStore = shell.rules
    subscribe(store.state)
    palette = palette_for(shell.page)
    ft.use_effect(lambda: _connect(shell), [])
    view = shell.state.layout
    intro = ft.Text(rules.intro_text(), style=text_style(TYPE.meta, color=palette.on_surface_variant))
    if view.two_pane:
        return hub_frame(shell, "rules", RuleWorkspace(), title=rules.page_title())
    body = ft.Column(expand=True, spacing=Space.MD, controls=[intro, PageBindings(), RuleList()])
    return PageFrame(title=rules.page_title(), body=body)


@ft.component
def RulePage(rule_id: str):
    shell = use_shell()
    store: RulesStore = shell.rules
    subscribe(store.state)
    ft.use_effect(lambda: _connect(shell), [])
    if store.loaded and store.state.selected != rule_id and store.rule(rule_id):
        store.select(rule_id)
    rule = store.rule(rule_id)
    title = rules.display_name(rule) if rule else rules.page_title()
    return PageFrame(title=title or rules.new_rule_name(), body=RuleEditor(key=f"editor:{rule_id}"), max_width=layout.MAX_WIDTH_FORM)


@ft.component
def RulesPage(destination: str, path: str):
    shell = use_shell()
    route = routes.parse(path)
    if route.parts and not shell.state.layout.two_pane:
        return RulePage(route.parts[0], key=f"rule:{route.parts[0]}")
    store = shell.rules
    if route.parts and store.loaded and route.parts[0] != store.state.selected and store.rule(route.parts[0]):
        store.select(route.parts[0])
    return RulesOverview()

