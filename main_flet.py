"""IwaraTool — Flet interface entry point (v1.3.0 trial; see docs/flet_ui_plan.md).

    pixi run -e flet start-flet

For now this opens the empty shell: theme, navigation and placeholder pages.
"""
import json
import os
import sys
from pathlib import Path

from app.ui_flet.runner import loaded_qt_modules, prepare_environment, run

# Before the rest of ``app`` is imported (the core reads these at import time).
prepare_environment(Path(__file__).resolve().parent)

import flet as ft  # noqa: E402

from app.config import app_config  # noqa: E402
from app.i18n import current_language, tr  # noqa: E402
from app.paths import app_data_dir  # noqa: E402
from app.ui_flet.icons import NavIcon  # noqa: E402
from app.ui_flet.theme import apply_theme  # noqa: E402
from app.ui_flet.tokens import Size, Space  # noqa: E402
from app.version import __version__  # noqa: E402

# Smoke test: ``--smoke-test`` or IWARATOOL_SMOKE_TEST=1 (packaged apps may not
# pass arguments). With IWARATOOL_SMOKE_REPORT set, a JSON report is written there.
SMOKE_TEST = "--smoke-test" in sys.argv or os.environ.get("IWARATOOL_SMOKE_TEST") == "1"


def write_smoke_report(page: ft.Page) -> None:
    target = os.environ.get("IWARATOOL_SMOKE_REPORT")
    if not target:
        return
    report = {
        "version": __version__,
        "python": sys.version.split()[0],
        "platform": str(page.platform.value if page.platform else ""),
        "packaged": bool(os.environ.get("FLET_PLATFORM")),
        "data_dir": str(app_data_dir()),
        "flet_storage": os.environ.get("FLET_APP_STORAGE_DATA", ""),
        "qt_modules": loaded_qt_modules(),
    }
    Path(target).write_text(json.dumps(report, indent=2), encoding="utf-8")


def destinations() -> list[tuple[NavIcon, str]]:
    return [
        (NavIcon.HOME, tr("Home", "首页", "ホーム")),
        (NavIcon.SUBSCRIPTIONS, tr("Subscriptions", "订阅", "購読")),
        (NavIcon.SEARCH, tr("Search", "搜索", "検索")),
        (NavIcon.DOWNLOADS, tr("Downloads", "下载", "ダウンロード")),
        (NavIcon.LIBRARY, tr("Library", "资料库", "ライブラリ")),
        (NavIcon.RULES, tr("Rules", "规则", "ルール")),
        (NavIcon.REPAIR, tr("Repair", "修复", "修復")),
        (NavIcon.TOOLS, tr("Tools", "工具", "ツール")),
        (NavIcon.SETTINGS, tr("Settings", "设置", "設定")),
    ]


def placeholder(title: str) -> ft.Control:
    return ft.Container(
        expand=True,
        padding=ft.Padding.symmetric(horizontal=Space.XXL, vertical=Space.XL),
        content=ft.Column(
            spacing=Space.SM,
            controls=[
                ft.Text(title, theme_style=ft.TextThemeStyle.HEADLINE_MEDIUM),
                ft.Text(
                    tr("This page has not been ported yet.", "此页面尚在移植中。", "このページは移植中です。"),
                    theme_style=ft.TextThemeStyle.BODY_MEDIUM,
                ),
            ],
        ),
    )


async def main(page: ft.Page) -> None:
    page.title = "IwaraTool"
    page.padding = 0
    page.window.min_width, page.window.min_height = 360, 560
    page.window.width, page.window.height = 1280, 800
    apply_theme(
        page,
        theme_mode=app_config.theme_mode,
        language=current_language(),
        user_font=str(app_config.ui_font_family or ""),
    )
    items = destinations()
    content = ft.Container(expand=True, content=placeholder(items[0][1]))

    def navigate(e: ft.Event[ft.NavigationRail]) -> None:
        content.content = placeholder(items[e.control.selected_index][1])
        page.update()

    page.add(
        ft.Row(
            expand=True,
            spacing=0,
            controls=[
                ft.NavigationRail(
                    selected_index=0,
                    label_type=ft.NavigationRailLabelType.ALL,
                    min_width=Size.NAV_RAIL,
                    on_change=navigate,
                    destinations=[
                        ft.NavigationRailDestination(icon=pair.rest, selected_icon=pair.active, label=label)
                        for pair, label in items
                    ],
                ),
                ft.VerticalDivider(width=1),
                content,
            ],
        )
    )
    if SMOKE_TEST:
        write_smoke_report(page)
        await page.window.destroy()


if __name__ == "__main__":
    run(main)
