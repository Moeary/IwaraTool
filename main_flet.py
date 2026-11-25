"""IwaraTool — Flet interface entry point (v1.3.0 trial; see docs/flet_ui_plan.md).

    pixi run -e flet start-flet

For now this opens the empty shell: theme, navigation and placeholder pages.
"""
import os
import sys
from pathlib import Path

# Before anything from ``app`` is imported: the core must never pick the Qt
# event backend in this interface.
os.environ["IWARATOOL_NO_QT"] = "1"
if not getattr(sys, "frozen", False) and not os.environ.get("IWARATOOL_DATA_DIR"):
    # A source checkout keeps its own settings, queue and history, apart from
    # the PySide6 interface's data folder.
    os.environ["IWARATOOL_DATA_DIR"] = str(Path(__file__).resolve().parent / "data-flet")

import flet as ft  # noqa: E402

from app.config import app_config  # noqa: E402
from app.i18n import current_language, tr  # noqa: E402
from app.ui_flet.icons import NavIcon  # noqa: E402
from app.ui_flet.runner import run  # noqa: E402
from app.ui_flet.theme import apply_theme  # noqa: E402
from app.ui_flet.tokens import Size, Space  # noqa: E402


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
    if "--smoke-test" in sys.argv:
        await page.window.destroy()


if __name__ == "__main__":
    run(main)
