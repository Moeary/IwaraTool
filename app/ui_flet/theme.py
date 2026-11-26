"""Turn the design tokens into Flet themes and apply them to a page.

``apply_theme`` sets ``page.theme`` (light), ``page.dark_theme`` and
``page.theme_mode`` once; every Material control then takes its colors,
shapes and text styles from there.  Pages only reach for ``palette_for`` and
``text_style`` when they draw something Material has no role for (status
badges, cover overlays, page titles).

Desktop (Windows, macOS) is the first target: compact visual density, CJK
fallback fonts per platform, no Material surface tint.
"""

from __future__ import annotations

import flet as ft

from ..presentation.fonts import cjk_fallback
from . import tokens as tk
from .tokens import TYPE, Mode, Palette, Radius, Size, Space, TypeRole

DESKTOP_PLATFORMS = frozenset({ft.PagePlatform.WINDOWS, ft.PagePlatform.MACOS, ft.PagePlatform.LINUX})

_THEME_MODES = {
    "auto": ft.ThemeMode.SYSTEM,
    "light": ft.ThemeMode.LIGHT,
    "dark": ft.ThemeMode.DARK,
}

_WEIGHTS = {w: getattr(ft.FontWeight, f"W_{w}") for w in range(100, 1000, 100)}


def font_platform(platform: ft.PagePlatform | None) -> str:
    """Key into the shared font tables for a Flet platform."""

    if platform == ft.PagePlatform.WINDOWS:
        return "windows"
    if platform in (ft.PagePlatform.MACOS, ft.PagePlatform.IOS):
        return "macos"
    return "linux"


def text_style(
    role: TypeRole,
    *,
    color: str | None = None,
    fallback: list[str] | None = None,
    family: str | None = None,
) -> ft.TextStyle:
    return ft.TextStyle(
        size=role.size,
        weight=_WEIGHTS[role.weight],
        height=role.line_height,
        color=color,
        font_family=family,
        font_family_fallback=fallback,
    )


def _text_theme(fallback: list[str], family: str | None, color: str) -> ft.TextTheme:
    """Material text roles, sized from §5.3.

    Material widgets read these roles directly (buttons use label_large,
    inputs and list titles body_large, dialogs headline_small, app bars
    title_large), so every role gets an explicit size, color and the CJK
    fallback.  Without a color Flet leaves plain ``Text`` white on the light
    theme; controls with their own foreground (buttons, tabs, chips) still
    override it.
    """

    def s(role: TypeRole) -> ft.TextStyle:
        return text_style(role, color=color, fallback=fallback, family=family)

    display = TypeRole(36, 400, 1.22)  # not used by the design; kept sane for stray widgets
    return ft.TextTheme(
        display_large=s(TypeRole(57, 400, 1.12)),
        display_medium=s(TypeRole(45, 400, 1.16)),
        display_small=s(display),
        headline_large=s(TypeRole(32, 600, 1.25)),
        headline_medium=s(TYPE.page_title),
        headline_small=s(TYPE.page_title_compact),
        title_large=s(TYPE.section_title),
        title_medium=s(TYPE.subtitle),
        title_small=s(TYPE.card_title),
        body_large=s(TYPE.body),
        body_medium=s(TYPE.body),
        body_small=s(TYPE.meta),
        label_large=s(TYPE.label),
        label_medium=s(TypeRole(TYPE.meta.size, 500, TYPE.meta.line_height)),
        label_small=s(TYPE.badge),
    )


def _color_scheme(p: Palette) -> ft.ColorScheme:
    return ft.ColorScheme(
        primary=p.primary,
        on_primary=p.on_primary,
        primary_container=p.primary_container,
        on_primary_container=p.on_primary_container,
        # Selection everywhere (rail and bar indicators, tonal buttons, selected
        # segments) uses the primary container, so one selected look app-wide.
        secondary_container=p.primary_container,
        on_secondary_container=p.on_primary_container,
        error=p.danger.fg,
        on_error=p.on_primary,
        error_container=p.danger.bg,
        on_error_container=p.danger.fg,
        surface=p.surface,
        on_surface=p.on_surface,
        on_surface_variant=p.on_surface_variant,
        surface_container_lowest=p.lowest,
        surface_container_low=p.card,
        surface_container=p.container,
        surface_container_high=p.raised,
        surface_container_highest=p.field,
        outline=p.outline,
        outline_variant=p.outline_variant,
        shadow=p.scrim,
        scrim=p.scrim,
        inverse_surface=p.inverse_surface,
        on_inverse_surface=p.on_inverse_surface,
        inverse_primary=p.inverse_primary,
        surface_tint=ft.Colors.TRANSPARENT,  # flat surfaces: no Material elevation tint
    )


def _rounded(radius: float) -> ft.RoundedRectangleBorder:
    return ft.RoundedRectangleBorder(radius=radius)


def build_theme(
    mode: Mode,
    *,
    platform: ft.PagePlatform | None = None,
    language: str = "zh",
    user_font: str = "",
) -> ft.Theme:
    """The full Flet theme for one brightness."""

    p = tk.palette(mode)
    family = user_font.strip() or None  # None: the platform UI font (Segoe UI / SF)
    fallback = cjk_fallback(font_platform(platform), language)
    desktop = platform is None or platform in DESKTOP_PLATFORMS
    hover = ft.Colors.with_opacity(0.06, p.on_surface)
    # Every style handed to the theme carries a color: Flet draws a theme text
    # style without one in white, whatever the brightness.  Controls with a
    # foreground of their own (buttons, tabs) still recolor their label.
    label = text_style(TYPE.label, color=p.on_surface, fallback=fallback, family=family)
    meta = text_style(TYPE.meta, color=p.on_surface_variant, fallback=fallback, family=family)
    body = text_style(TYPE.body, color=p.on_surface, fallback=fallback, family=family)
    nav_selected = text_style(
        TypeRole(TYPE.meta.size, 600, TYPE.meta.line_height), color=p.on_surface, fallback=fallback, family=family
    )
    nav_unselected = text_style(
        TypeRole(TYPE.meta.size, 500, TYPE.meta.line_height), color=p.on_surface_variant, fallback=fallback, family=family
    )

    button_shape = _rounded(Radius.CONTROL)
    button_style = ft.ButtonStyle(
        shape=button_shape,
        padding=ft.Padding.symmetric(horizontal=Space.LG, vertical=Space.MD if not desktop else Space.SM + 2),
        text_style=label,
        icon_size=Size.ICON,
    )

    return ft.Theme(
        color_scheme_seed=tk.BRAND_SEED,
        color_scheme=_color_scheme(p),
        use_material3=True,
        font_family=family,
        text_theme=_text_theme(fallback, family, p.on_surface),
        visual_density=ft.VisualDensity.COMPACT if desktop else ft.VisualDensity.STANDARD,
        scaffold_bgcolor=p.surface,
        canvas_color=p.surface,
        card_bgcolor=p.card,
        divider_color=p.outline_variant,
        hint_color=p.on_surface_variant,
        disabled_color=p.disabled,
        unselected_control_color=p.on_surface_variant,
        hover_color=hover,
        focus_color=ft.Colors.with_opacity(0.10, p.primary),
        card_theme=ft.CardTheme(
            color=p.card,
            elevation=0,
            shadow_color=ft.Colors.TRANSPARENT,
            shape=_rounded(Radius.CARD),
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            margin=0,
        ),
        button_theme=ft.ButtonTheme(style=button_style),
        filled_button_theme=ft.FilledButtonTheme(style=button_style),
        outlined_button_theme=ft.OutlinedButtonTheme(
            style=ft.ButtonStyle(
                shape=button_shape,
                padding=button_style.padding,
                text_style=label,
                icon_size=Size.ICON,
                side=ft.BorderSide(1, p.outline_variant),
            )
        ),
        text_button_theme=ft.TextButtonTheme(style=button_style),
        icon_button_theme=ft.IconButtonTheme(
            style=ft.ButtonStyle(shape=button_shape, icon_size=Size.ICON, icon_color=p.on_surface_variant)
        ),
        floating_action_button_theme=ft.FloatingActionButtonTheme(
            bgcolor=p.primary,
            foreground_color=p.on_primary,
            elevation=2,
            hover_elevation=3,
            focus_elevation=3,
            highlight_elevation=2,
            shape=_rounded(Radius.FAB),
            text_style=label,
        ),
        segmented_button_theme=ft.SegmentedButtonTheme(
            style=ft.ButtonStyle(shape=button_shape, text_style=label, side=ft.BorderSide(1, p.outline_variant))
        ),
        input_decoration_theme=ft.InputDecorationTheme(
            filled=True,
            fill_color=p.field,
            hover_color=ft.Colors.with_opacity(0.04, p.on_surface),
            border=ft.OutlineInputBorder(
                border_radius=Radius.CONTROL,
                side=ft.BorderSide(1, ft.Colors.TRANSPARENT),
            ),
            content_padding=ft.Padding.symmetric(horizontal=Space.MD, vertical=Space.SM + 2),
            hint_style=text_style(TYPE.body, color=p.on_surface_variant, fallback=fallback, family=family),
            label_style=meta,
            helper_style=meta,
            error_style=text_style(TYPE.meta, color=p.danger.fg, fallback=fallback, family=family),
            prefix_icon_color=p.on_surface_variant,
            suffix_icon_color=p.on_surface_variant,
            dense=desktop,
        ),
        search_bar_theme=ft.SearchBarTheme(
            bgcolor=p.field,
            elevation=0,
            shadow_color=ft.Colors.TRANSPARENT,
            shape=button_shape,
            text_style=body,
            hint_style=text_style(TYPE.body, color=p.on_surface_variant, fallback=fallback, family=family),
        ),
        chip_theme=ft.ChipTheme(
            bgcolor=p.field,
            selected_color=p.primary_container,
            shape=_rounded(Radius.CHIP),
            border_side=ft.BorderSide(0, ft.Colors.TRANSPARENT),
            label_text_style=label,
            show_checkmark=False,
            elevation=0,
            padding=ft.Padding.symmetric(horizontal=Space.XS, vertical=0),
        ),
        navigation_rail_theme=ft.NavigationRailTheme(
            bgcolor=p.surface,
            indicator_color=p.primary_container,
            indicator_shape=ft.StadiumBorder(),
            selected_label_text_style=nav_selected,
            unselected_label_text_style=nav_unselected,
            min_width=Size.NAV_RAIL,
            min_extended_width=Size.NAV_RAIL_EXTENDED,
            use_indicator=True,
            elevation=0,
        ),
        navigation_bar_theme=ft.NavigationBarTheme(
            bgcolor=p.container,
            indicator_color=p.primary_container,
            indicator_shape=ft.StadiumBorder(),
            shadow_color=ft.Colors.TRANSPARENT,
            elevation=0,
            height=Size.NAV_BAR,
            label_text_style={ft.ControlState.SELECTED: nav_selected, ft.ControlState.DEFAULT: nav_unselected},
            label_behavior=ft.NavigationBarLabelBehavior.ALWAYS_SHOW,
        ),
        appbar_theme=ft.AppBarTheme(
            bgcolor=p.surface,
            color=p.on_surface,
            elevation=0,
            elevation_on_scroll=0,
            shadow_color=ft.Colors.TRANSPARENT,
            center_title=False,
            title_text_style=text_style(TYPE.page_title_compact, color=p.on_surface, fallback=fallback, family=family),
            toolbar_height=56,
        ),
        list_tile_theme=ft.ListTileTheme(
            shape=_rounded(Radius.CONTROL),
            selected_tile_color=p.primary_container,
            selected_color=p.on_primary_container,
            icon_color=p.on_surface_variant,
            content_padding=ft.Padding.symmetric(horizontal=Space.MD, vertical=0),
            min_height=Size.ROW_DESKTOP if desktop else Size.TOUCH_TARGET + 8,
            title_text_style=text_style(TYPE.body, color=p.on_surface, fallback=fallback, family=family),
            subtitle_text_style=text_style(TYPE.meta, color=p.on_surface_variant, fallback=fallback, family=family),
        ),
        dialog_theme=ft.DialogTheme(
            bgcolor=p.raised,
            shape=_rounded(Radius.DIALOG),
            elevation=3,
            title_text_style=text_style(TYPE.section_title, color=p.on_surface, fallback=fallback, family=family),
            content_text_style=text_style(TYPE.body, color=p.on_surface_variant, fallback=fallback, family=family),
            barrier_color=ft.Colors.with_opacity(0.4, p.scrim),
        ),
        bottom_sheet_theme=ft.BottomSheetTheme(
            bgcolor=p.raised,
            elevation=1,
            shape=ft.RoundedRectangleBorder(
                radius=ft.BorderRadius.only(top_left=Radius.SHEET, top_right=Radius.SHEET)
            ),
            show_drag_handle=True,
            drag_handle_color=p.outline,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            barrier_color=ft.Colors.with_opacity(0.4, p.scrim),
        ),
        popup_menu_theme=ft.PopupMenuTheme(
            color=p.raised,
            elevation=3,
            shape=_rounded(Radius.CONTROL),
            icon_color=p.on_surface_variant,
            icon_size=Size.ICON,
            label_text_style=text_style(TYPE.body, color=p.on_surface, fallback=fallback, family=family),
        ),
        snackbar_theme=ft.SnackBarTheme(
            bgcolor=p.inverse_surface,
            content_text_style=text_style(TYPE.body, color=p.on_inverse_surface, fallback=fallback, family=family),
            action_text_color=p.inverse_primary,
            close_icon_color=p.on_inverse_surface,
            behavior=ft.SnackBarBehavior.FLOATING,
            shape=_rounded(Radius.CONTROL),
            elevation=2,
            width=480 if desktop else None,
        ),
        tooltip_theme=ft.TooltipTheme(
            text_style=text_style(TYPE.meta, color=p.on_inverse_surface, fallback=fallback, family=family),
            decoration=ft.BoxDecoration(bgcolor=p.inverse_surface, border_radius=Radius.BADGE),
            padding=ft.Padding.symmetric(horizontal=Space.SM, vertical=Space.XS),
            wait_duration=ft.Duration(milliseconds=500),
            prefer_below=True,
        ),
        divider_theme=ft.DividerTheme(color=p.outline_variant, thickness=1, space=1),
        progress_indicator_theme=ft.ProgressIndicatorTheme(
            color=p.primary,
            linear_track_color=p.field,
            circular_track_color=p.field,
            linear_min_height=4,
            border_radius=Radius.FULL,
            stop_indicator_radius=0,  # no dot at the end of the track
        ),
        tab_bar_theme=ft.TabBarTheme(
            indicator_color=p.primary,
            label_color=p.primary,
            unselected_label_color=p.on_surface_variant,
            divider_color=ft.Colors.TRANSPARENT,
            label_text_style=label,
            unselected_label_text_style=label,
        ),
        badge_theme=ft.BadgeTheme(
            bgcolor=p.primary,
            text_color=p.on_primary,
            text_style=text_style(TYPE.badge, color=p.on_primary, fallback=fallback, family=family),
        ),
        checkbox_theme=ft.CheckboxTheme(shape=_rounded(4)),
        scrollbar_theme=ft.ScrollbarTheme(
            thickness=6,
            radius=3,
            thumb_color=ft.Colors.with_opacity(0.32, p.on_surface),
            main_axis_margin=2,
            cross_axis_margin=2,
        ),
        icon_theme=ft.IconTheme(color=p.on_surface_variant, size=Size.ICON),
    )


def resolve_mode(page: ft.Page) -> Mode:
    """The brightness the page shows right now (``auto`` follows the system)."""

    if page.theme_mode == ft.ThemeMode.DARK:
        return "dark"
    if page.theme_mode == ft.ThemeMode.LIGHT:
        return "light"
    return "dark" if page.platform_brightness == ft.Brightness.DARK else "light"


def palette_for(page: ft.Page) -> Palette:
    """Token palette for colors Material has no role for (status badges, cover overlays)."""

    return tk.palette(resolve_mode(page))


def apply_theme(page: ft.Page, *, theme_mode: str = "auto", language: str = "zh", user_font: str = "") -> None:
    """Install both themes on ``page`` and choose the mode (``auto`` / ``light`` / ``dark``).

    Call again after the language or font changes: the CJK fallback order
    depends on the language.  The caller updates the page.
    """

    options = dict(platform=page.platform, language=language, user_font=user_font)
    page.theme = build_theme("light", **options)
    page.dark_theme = build_theme("dark", **options)
    page.theme_mode = _THEME_MODES.get(theme_mode, ft.ThemeMode.SYSTEM)
    page.bgcolor = None  # let the scaffold color of the active theme show
