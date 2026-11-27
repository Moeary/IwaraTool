"""Design tokens, theme and icon registry of the interface, and the layering guards.

Token and source checks need no UI framework; the theme and icon checks run
where Flet is installed (``pixi run test``).
"""

from __future__ import annotations

import importlib.util
import re
import unittest
from pathlib import Path

from app.presentation.fonts import build_font_stack, cjk_fallback
from app.ui import tokens as tk

ROOT = Path(__file__).resolve().parents[3]
UI = ROOT / "app" / "ui"
PRESENTATION = ROOT / "app" / "presentation"
CORE = ROOT / "app" / "core"
HAS_FLET = importlib.util.find_spec("flet") is not None

AA_TEXT = 4.5  # WCAG AA, normal text
AA_UI = 3.0  # WCAG AA, icons and component boundaries


class TokenContrastTests(unittest.TestCase):
    def assertContrast(self, fg: str, bg: str, minimum: float, label: str):
        ratio = tk.contrast_ratio(fg, bg)
        self.assertGreaterEqual(ratio, minimum, f"{label}: {fg} on {bg} is {ratio:.2f}, needs {minimum}")

    def test_every_color_is_hex(self):
        for mode in ("light", "dark"):
            for name, value in tk.color_fields(tk.palette(mode)).items():
                self.assertRegex(value, r"^#([0-9A-F]{6}|[0-9A-F]{8})$", f"{mode} {name}")

    def test_text_on_surfaces(self):
        for mode in ("light", "dark"):
            p = tk.palette(mode)
            for surface in ("surface", "card", "container", "raised", "field"):
                bg = getattr(p, surface)
                self.assertContrast(p.on_surface, bg, AA_TEXT, f"{mode} on_surface/{surface}")
                self.assertContrast(p.on_surface_variant, bg, AA_TEXT, f"{mode} on_surface_variant/{surface}")

    def test_primary_roles(self):
        for mode in ("light", "dark"):
            p = tk.palette(mode)
            self.assertContrast(p.on_primary, p.primary, AA_TEXT, f"{mode} on_primary")
            self.assertContrast(p.on_primary_container, p.primary_container, AA_TEXT, f"{mode} on_primary_container")
            for surface in ("surface", "card"):
                # text buttons and links are primary-colored text
                self.assertContrast(p.primary, getattr(p, surface), AA_TEXT, f"{mode} primary/{surface}")

    def test_inverse_roles_for_snack_bars_and_tooltips(self):
        for mode in ("light", "dark"):
            p = tk.palette(mode)
            self.assertContrast(p.on_inverse_surface, p.inverse_surface, AA_TEXT, f"{mode} on_inverse_surface")
            self.assertContrast(p.inverse_primary, p.inverse_surface, AA_TEXT, f"{mode} inverse_primary")

    def test_text_on_covers(self):
        for mode in ("light", "dark"):
            p = tk.palette(mode)
            # the scrim is translucent black: check it over a white cover, the worst case
            scrim = tk.blend(p.cover_scrim, "#FFFFFF")
            self.assertContrast(p.on_cover_scrim, scrim, AA_TEXT, f"{mode} on_cover_scrim")

    def test_semantic_colors(self):
        for mode in ("light", "dark"):
            p = tk.palette(mode)
            for name in ("success", "warning", "danger", "info"):
                s = getattr(p, name)
                self.assertContrast(s.fg, s.bg, AA_TEXT, f"{mode} {name} badge")
                self.assertContrast(s.fg, p.card, AA_UI, f"{mode} {name} icon on card")

    def test_spacing_is_on_the_4dp_grid(self):
        for name, value in vars(tk.Space).items():
            if not name.startswith("_") and name != "XXS":
                self.assertEqual(value % 4, 0, name)


class FontFallbackTests(unittest.TestCase):
    def test_language_decides_cjk_order(self):
        self.assertEqual(cjk_fallback("windows", "zh")[0], "Microsoft YaHei UI")
        self.assertEqual(cjk_fallback("windows", "ja")[0], "Yu Gothic UI")
        self.assertEqual(cjk_fallback("macos", "zh")[0], "PingFang SC")
        self.assertEqual(cjk_fallback("macos", "ja")[0], "Hiragino Sans")

    def test_fallback_holds_both_scripts_once(self):
        stack = cjk_fallback("macos", "en")
        self.assertIn("Hiragino Sans", stack)
        self.assertEqual(len(stack), len({name.casefold() for name in stack}))

    def test_shared_stack_matches_the_legacy_order(self):
        # The order the v1.2 interface used (app/ui/style/fonts.py there).
        self.assertEqual(
            build_font_stack("windows", "ja", system_family="Segoe UI"),
            ["Segoe UI", "Yu Gothic UI", "Microsoft YaHei UI"],
        )


def _sources(folder: Path):
    for path in sorted(folder.rglob("*.py")):
        yield path, path.read_text(encoding="utf-8")


class SourceGuardTests(unittest.TestCase):
    QT_IMPORT = re.compile(r"^\s*(from|import)\s+(PySide6|shiboken6|qfluentwidgets|qframelesswindow)\b", re.MULTILINE)
    UI_IMPORT = re.compile(r"^\s*(from|import)\s+(app\.ui\b|\.\.ui\b|\.\.\.ui\b)", re.MULTILINE)
    FLET_IMPORT = re.compile(r"^\s*(from|import)\s+flet\b", re.MULTILINE)

    def test_sources_are_checked(self):
        self.assertGreater(len(list(_sources(UI))), 50)  # the guards below must not pass on an empty folder

    def test_interface_never_imports_qt(self):
        for path, text in _sources(UI):
            self.assertIsNone(self.QT_IMPORT.search(text), path.relative_to(ROOT))

    def test_presentation_and_core_import_no_ui_framework(self):
        for folder in (PRESENTATION, CORE):
            for path, text in _sources(folder):
                rel = path.relative_to(ROOT)
                self.assertIsNone(self.QT_IMPORT.search(text), rel)
                self.assertIsNone(self.UI_IMPORT.search(text), rel)
                self.assertIsNone(self.FLET_IMPORT.search(text), rel)

    def test_tokens_import_no_ui_framework(self):
        text = (UI / "tokens.py").read_text(encoding="utf-8")
        self.assertIsNone(self.FLET_IMPORT.search(text))

    def test_icons_come_only_from_the_registry(self):
        for path, text in _sources(UI):
            if path.name == "icons.py":
                continue
            self.assertNotRegex(text, r"\bft\.(Cupertino)?Icons\.", path.relative_to(ROOT))

    def test_colors_come_only_from_the_tokens(self):
        for path, text in _sources(UI):
            if path.name == "tokens.py":
                continue
            self.assertNotRegex(text, r"[\"']#[0-9A-Fa-f]{3,8}[\"']", path.relative_to(ROOT))


@unittest.skipUnless(HAS_FLET, "Flet is not installed in this environment")
class FletThemeTests(unittest.TestCase):
    def test_theme_builds_for_every_mode_and_platform(self):
        import flet as ft

        from app.ui.theme import build_theme

        for mode in ("light", "dark"):
            for platform in (ft.PagePlatform.WINDOWS, ft.PagePlatform.MACOS, ft.PagePlatform.LINUX, ft.PagePlatform.ANDROID):
                theme = build_theme(mode, platform=platform, language="ja")
                p = tk.palette(mode)
                self.assertEqual(theme.color_scheme.primary, p.primary)
                self.assertEqual(theme.scaffold_bgcolor, p.surface)
                desktop = platform != ft.PagePlatform.ANDROID
                self.assertEqual(
                    theme.visual_density, ft.VisualDensity.COMPACT if desktop else ft.VisualDensity.STANDARD
                )
                # CJK fallback follows the language on every text role
                self.assertEqual(
                    theme.text_theme.body_medium.font_family_fallback[0],
                    cjk_fallback("windows" if platform == ft.PagePlatform.WINDOWS else
                                 "macos" if platform == ft.PagePlatform.MACOS else "linux", "ja")[0],
                )

    def test_every_theme_text_style_has_a_color(self):
        # Flet draws a theme text style without a color in white, even on the light theme.
        import dataclasses

        import flet as ft

        from app.ui.theme import build_theme

        def walk(value, path):
            if isinstance(value, ft.TextStyle):
                self.assertIsNotNone(value.color, path)
            elif isinstance(value, dict):
                for key, item in value.items():
                    walk(item, f"{path}[{key}]")
            elif dataclasses.is_dataclass(value) and not isinstance(value, type):
                for field in dataclasses.fields(value):
                    walk(getattr(value, field.name), f"{path}.{field.name}")

        for mode in ("light", "dark"):
            walk(build_theme(mode), mode)

    def test_user_font_leads(self):
        from app.ui.theme import build_theme

        self.assertEqual(build_theme("light", user_font="LXGW WenKai").font_family, "LXGW WenKai")
        self.assertIsNone(build_theme("light").font_family)

    def test_every_registered_icon_exists(self):
        import flet as ft

        from app.ui.icons import all_icons

        icons = all_icons()
        self.assertGreater(len(icons), 100)
        for name, icon in icons.items():
            self.assertIsInstance(icon, ft.IconData, name)

    def test_navigation_icons_change_when_selected(self):
        from app.ui.icons import NavIcon

        for name, pair in vars(NavIcon).items():
            if not name.startswith("_"):
                self.assertNotEqual(pair.rest, pair.active, name)

    def test_status_badges_cover_every_kind(self):
        from app.ui.components.status_badge import BadgeKind, badge_colors, status_badge

        for mode in ("light", "dark"):
            p = tk.palette(mode)
            for kind in BadgeKind:
                colors = badge_colors(kind, p)
                self.assertGreaterEqual(tk.contrast_ratio(colors.fg, colors.bg), AA_TEXT, f"{mode} {kind}")
                self.assertIsNotNone(status_badge(kind, p))


if __name__ == "__main__":
    unittest.main()
