"""Customize Home (``/settings/home/rows``): which rows show, in what order, and what each searches.

The old window used two dialogs; here the list and the row editor share one
page (the editor sits under the list on phones).  Edits stay a draft until
Save, like before, so the Home page reloads once.  Labels, the editor's
rules and the built-in-row handling are ``presentation.home.layout``'s.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

import flet as ft

from ....core.home.feed import MODE_BROWSE, HomeSectionSpec, default_specs, load_specs, save_specs
from ....presentation.home import layout as rows_rules
from ... import layout
from ...components.page_frame import PageFrame
from ...icons import AppIcon, StateIcon
from ...services.dialogs import confirm
from ...stores.app_state import use_shell
from ...theme import palette_for, text_style
from ...tokens import TYPE, Radius, Size, Space
from .rows import dropdown, hint


@dataclass(frozen=True)
class Form:
    index: int  # -1 adds a row
    mode: str = MODE_BROWSE
    content: str = "video"
    title: str = ""
    value: str = ""
    sort: str = ""
    error: str = ""


def _form_for(index: int, spec: HomeSectionSpec | None) -> Form:
    if spec is None:
        return Form(index=-1, sort=rows_rules.pick_sort(MODE_BROWSE, ""))
    return Form(index=index, mode=spec.mode, content=spec.content, title=spec.title, value=spec.value,
                sort=rows_rules.pick_sort(spec.mode, spec.sort))


@ft.component
def RowEditor(form: Form, original: HomeSectionSpec | None, on_change, on_done, on_cancel):
    shell = use_shell()
    page = shell.page
    palette = palette_for(page)
    labels = rows_rules.field_labels()
    texts = rows_rules.list_texts()
    mode = form.mode

    def set_mode(value: str) -> None:
        on_change(replace(form, mode=value, sort=rows_rules.pick_sort(value, form.sort), error=""))

    def save(_e=None) -> None:
        spec = rows_rules.build_spec(original, mode=form.mode, content=form.content, title=form.title,
                                     value=form.value, sort=form.sort)
        if spec is None:
            on_change(replace(form, error=rows_rules.missing_value_text()))
            return
        on_done(form.index, spec)

    caption, placeholder = rows_rules.value_caption(mode)
    controls: list[ft.Control | None] = [
        ft.Text(rows_rules.editor_title(original is not None), style=text_style(TYPE.subtitle)),
        ft.TextField(label=labels["title"], hint_text=labels["title_hint"], value=form.title, dense=True,
                     on_change=lambda e: on_change(replace(form, title=e.control.value or ""))),
        dropdown(mode, [(m, rows_rules.mode_label(m)) for m in rows_rules.MODES], set_mode, width=280),
        dropdown(form.content, rows_rules.content_choices(), lambda v: on_change(replace(form, content=v)), width=280)
        if rows_rules.shows_content(mode) else None,
        ft.TextField(label=caption, hint_text=placeholder, value=form.value, dense=True, autofocus=original is None,
                     error=form.error or None, on_submit=save,
                     on_change=lambda e: on_change(replace(form, value=e.control.value or "", error="")))
        if rows_rules.needs_value(mode) else None,
        dropdown(form.sort, rows_rules.sort_choices(mode), lambda v: on_change(replace(form, sort=v)), width=280)
        if rows_rules.shows_sort(mode) else None,
        hint(page, form.error if form.error and not rows_rules.needs_value(mode) else rows_rules.mode_hint(mode),
             danger=bool(form.error) and not rows_rules.needs_value(mode)),
        ft.Row(spacing=Space.SM, alignment=ft.MainAxisAlignment.END, controls=[
            ft.TextButton(content=texts["cancel"], on_click=lambda _e: on_cancel()),
            ft.FilledButton(content=texts["save"], icon=AppIcon.CHECK, on_click=save),
        ]),
    ]
    return ft.Container(padding=Space.LG, border_radius=Radius.CARD, bgcolor=palette.card,
                        border=ft.Border.all(1, palette.primary),
                        content=ft.Column(spacing=Space.MD, controls=[c for c in controls if c is not None]))


@ft.component
def HomeRowsPage():
    shell = use_shell()
    page = shell.page
    palette = palette_for(page)
    texts = rows_rules.list_texts()
    specs, set_specs = ft.use_state(load_specs)
    form, set_form = ft.use_state(None)
    saved = load_specs()
    dirty = [s.to_json() for s in specs] != [s.to_json() for s in saved]

    def toggle(index: int, value: bool) -> None:
        set_specs([replace(s, enabled=value) if i == index else s for i, s in enumerate(specs)])

    def remove(index: int) -> None:
        set_specs([s for i, s in enumerate(specs) if i != index])
        set_form(None)

    def done(index: int, spec: HomeSectionSpec) -> None:
        if index < 0:
            set_specs([*specs, spec])
        else:
            set_specs([spec if i == index else s for i, s in enumerate(specs)])
        set_form(None)

    async def reset() -> None:
        title, question = rows_rules.reset_question()
        if await confirm(page, title, question, confirm_label=texts["reset"]):
            set_specs(default_specs())
            set_form(None)

    def save(_e) -> None:
        from ....signal_bus import signal_bus

        save_specs(list(specs))
        signal_bus.home_layout_changed.emit()
        shell.back()

    def row(index: int, spec: HomeSectionSpec) -> ft.Control:
        editing = form is not None and form.index == index
        return ft.Container(
            padding=ft.Padding.symmetric(horizontal=Space.SM, vertical=Space.XS), border_radius=Radius.CONTROL,
            bgcolor=palette.primary_container if editing else palette.card,
            content=ft.Row(spacing=Space.XS, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
                ft.Checkbox(value=spec.enabled, on_change=lambda e, i=index: toggle(i, bool(e.control.value))),
                ft.Column(spacing=0, expand=True, controls=[
                    ft.Text(spec.display_title(), style=text_style(TYPE.label), max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                    ft.Text(rows_rules.spec_summary(spec), style=text_style(TYPE.meta, color=palette.on_surface_variant),
                            max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                ]),
                ft.IconButton(icon=StateIcon.EXPAND.active, tooltip=texts["up"], disabled=index == 0,
                              on_click=lambda _e, i=index: set_specs(rows_rules.move(specs, i, -1))),
                ft.IconButton(icon=StateIcon.EXPAND.rest, tooltip=texts["down"], disabled=index == len(specs) - 1,
                              on_click=lambda _e, i=index: set_specs(rows_rules.move(specs, i, 1))),
                ft.IconButton(icon=AppIcon.EDIT, tooltip=texts["edit"],
                              on_click=lambda _e, i=index, s=spec: set_form(_form_for(i, s))),
                ft.IconButton(icon=AppIcon.DELETE, tooltip=texts["remove"], on_click=lambda _e, i=index: remove(i)),
            ]),
        )

    editor = None
    if form is not None:
        original = specs[form.index] if 0 <= form.index < len(specs) else None
        editor = RowEditor(form, original, set_form, done, lambda: set_form(None), key=f"row-editor:{form.index}")
    note = rows_rules.empty_note(specs)
    body = ft.Column(spacing=Space.MD, controls=[
        hint(page, rows_rules.customize_hint()),
        *[row(i, s) for i, s in enumerate(specs)],
        *([hint(page, note, danger=True)] if note else []),
        ft.Row(wrap=True, spacing=Space.SM, controls=[
            ft.OutlinedButton(content=texts["add"], icon=AppIcon.ADD, disabled=len(specs) >= rows_rules.MAX_SECTIONS,
                              on_click=lambda _e: set_form(_form_for(-1, None))),
            ft.TextButton(content=texts["reset"], on_click=lambda _e: page.run_task(reset)),
        ]),
        *([editor] if editor is not None else []),
        ft.Container(height=Size.TOUCH_TARGET),
    ])
    actions = [ft.FilledButton(content=texts["save"], icon=AppIcon.SAVE, disabled=not dirty, on_click=save)]
    return PageFrame(title=rows_rules.customize_title(), body=ft.ListView(expand=True, controls=[body]),
                     actions=actions, max_width=layout.MAX_WIDTH_FORM)
