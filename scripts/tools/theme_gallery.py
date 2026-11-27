"""Theme gallery for the Flet interface: every token, common control and icon on one page.

    pixi run theme-gallery                       # interactive window
    pixi run theme-gallery --mode dark --lang ja
    pixi run theme-gallery --shot tmp/flet_theme/light.png   # save a screenshot and quit

Use it to judge a theme change before any page uses it.  Runs against a
throw-away data folder; the user's settings are never read or written.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("IWARATOOL_DATA_DIR", tempfile.mkdtemp(prefix="iwaratool-theme-"))

import flet as ft  # noqa: E402

from app.config import app_config  # noqa: E402
from app.i18n import tr  # noqa: E402
from app.ui import tokens as tk  # noqa: E402
from app.ui.components.status_badge import BadgeKind, status_badge  # noqa: E402
from app.ui.icons import AppIcon, NavIcon, StateIcon, all_icons  # noqa: E402
from app.ui.runner import run  # noqa: E402
from app.ui.theme import apply_theme, palette_for, text_style  # noqa: E402
from app.ui.tokens import TYPE, Radius, Size, Space  # noqa: E402

LANG_SETTING = {"zh": "zh_CN", "ja": "ja_JP", "en": "en_US"}
SAMPLE = "首页 订阅 下载 · ホーム 購読 · Home 1,234"


def noop(_=None) -> None:
    pass


def section(title: str, *controls: ft.Control) -> ft.Control:
    return ft.Card(
        content=ft.Container(
            padding=Space.LG,
            content=ft.Column(
                spacing=Space.MD,
                controls=[ft.Text(title, theme_style=ft.TextThemeStyle.TITLE_LARGE), *controls],
            ),
        )
    )


def swatches(p: tk.Palette) -> ft.Control:
    tiles = []
    for name, value in tk.color_fields(p).items():
        if value.startswith("#") and len(value) == 9:
            continue  # alpha overlays are not meaningful as swatches
        text_color = "#FFFFFF" if tk.relative_luminance(value) < 0.35 else "#000000"
        tiles.append(
            ft.Container(
                width=148,
                height=64,
                bgcolor=value,
                border_radius=Radius.CONTROL,
                border=ft.Border.all(1, p.outline_variant),
                padding=Space.SM,
                content=ft.Column(
                    spacing=0,
                    controls=[
                        ft.Text(name, size=12, weight=ft.FontWeight.W_600, color=text_color),
                        ft.Text(value, size=11, color=text_color),
                    ],
                ),
            )
        )
    return ft.Row(wrap=True, spacing=Space.SM, run_spacing=Space.SM, controls=tiles)


def type_scale(p: tk.Palette) -> ft.Control:
    rows = []
    for name in (
        "page_title", "page_title_compact", "section_title", "section_title_compact",
        "subtitle", "card_title", "body", "label", "meta", "badge",
    ):
        role = getattr(TYPE, name)
        rows.append(
            ft.Row(
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Text(f"{name}  {role.size:g}/{role.weight}", width=220, style=text_style(TYPE.meta, color=p.on_surface_variant)),
                    ft.Text(SAMPLE, style=text_style(role, color=p.on_surface), expand=True, no_wrap=True),
                ],
            )
        )
    return ft.Column(spacing=Space.XS, controls=rows)


def buttons() -> ft.Control:
    return ft.Row(
        wrap=True,
        spacing=Space.SM,
        run_spacing=Space.SM,
        controls=[
            ft.FilledButton(tr("Download", "下载", "ダウンロード"), icon=AppIcon.DOWNLOAD),
            ft.FilledTonalButton(tr("Add", "添加", "追加"), icon=AppIcon.ADD),
            ft.OutlinedButton(tr("Refresh all", "全部刷新", "すべて更新"), icon=AppIcon.REFRESH),
            ft.TextButton(tr("More", "查看更多", "もっと見る"), icon=AppIcon.CHEVRON_RIGHT),
            ft.FilledButton(tr("Disabled", "不可用", "無効"), disabled=True),
            ft.IconButton(AppIcon.SEARCH, tooltip=tr("Search", "搜索", "検索")),
            ft.IconButton(AppIcon.FILTER, tooltip=tr("Filter", "筛选", "フィルター")),
            ft.IconButton(AppIcon.MORE, tooltip=tr("More", "更多", "その他")),
            ft.IconButton(StateIcon.LIKE.rest, selected_icon=StateIcon.LIKE.active, selected=True),
            ft.FloatingActionButton(icon=AppIcon.PASTE, content=tr("Paste link", "粘贴链接", "リンクを貼り付け")),
        ],
    )


def inputs() -> ft.Control:
    return ft.Column(
        spacing=Space.MD,
        controls=[
            ft.Row(
                spacing=Space.MD,
                controls=[
                    ft.TextField(
                        hint_text=tr("Search title, author, tag…", "搜索标题、作者、标签…", "タイトル・作者・タグを検索…"),
                        prefix_icon=AppIcon.SEARCH,
                        expand=True,
                    ),
                    ft.Dropdown(
                        width=200,
                        value="latest",
                        options=[
                            ft.DropdownOption(key="latest", text=tr("Latest", "最新", "最新")),
                            ft.DropdownOption(key="trending", text=tr("Trending", "趋势", "トレンド")),
                        ],
                    ),
                ],
            ),
            ft.TextField(label=tr("Download folder", "下载目录", "保存先"), value="~/Downloads/IwaraTool",
                         error=tr("Folder does not exist", "目录不存在", "フォルダーがありません")),
            ft.Row(
                wrap=True,
                spacing=Space.SM,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.SegmentedButton(
                        selected=["all"],
                        segments=[
                            ft.Segment(value="all", label=ft.Text(tr("All", "全部", "すべて"))),
                            ft.Segment(value="sfw", label=ft.Text("SFW")),
                            ft.Segment(value="nsfw", label=ft.Text("NSFW")),
                        ],
                    ),
                    ft.Chip(label=ft.Text(tr("Videos", "视频", "動画")), leading=ft.Icon(AppIcon.VIDEO), selected=True, on_select=noop),
                    ft.Chip(label=ft.Text(tr("Images", "图片", "画像")), leading=ft.Icon(AppIcon.IMAGE), on_select=noop),
                    ft.Chip(label=ft.Text(tr("Sort: Latest", "排序：最新", "並び替え：最新")), leading=ft.Icon(AppIcon.SORT), on_click=noop),
                    ft.Switch(label=tr("Skip downloaded", "跳过已下载", "ダウンロード済みをスキップ"), value=True),
                    ft.Checkbox(label=tr("Remember", "记住", "記憶"), value=True),
                ],
            ),
            ft.Slider(value=0.4, width=320),
        ],
    )


def badges(p: tk.Palette) -> ft.Control:
    return ft.Column(
        spacing=Space.MD,
        controls=[
            ft.Row(
                wrap=True,
                spacing=Space.SM,
                controls=[
                    status_badge(BadgeKind.NEW, p),
                    status_badge(BadgeKind.QUEUED, p),
                    status_badge(BadgeKind.DOWNLOADING, p, text="42%"),
                    status_badge(BadgeKind.DOWNLOADED, p),
                    status_badge(BadgeKind.MOVED, p),
                    status_badge(BadgeKind.REMOTE, p),
                    status_badge(BadgeKind.FAILED, p),
                ],
            ),
            ft.ProgressBar(value=0.42, width=320),
        ],
    )


def video_card(p: tk.Palette, title: str, badge: ft.Control | None, progress: float | None = None) -> ft.Control:
    cover_layers: list[ft.Control] = [
        ft.Container(
            gradient=ft.LinearGradient(
                begin=ft.Alignment.TOP_LEFT, end=ft.Alignment.BOTTOM_RIGHT,
                colors=[p.primary_container, p.cover_placeholder],
            ),
        ),
        ft.Container(
            right=Space.SM, bottom=Space.SM,
            bgcolor=ft.Colors.with_opacity(0.7, p.scrim),
            border_radius=Radius.BADGE,
            padding=ft.Padding.symmetric(horizontal=Space.XS + 2, vertical=1),
            content=ft.Text("4:59", style=text_style(TYPE.badge, color="#FFFFFF")),
        ),
    ]
    if badge is not None:
        cover_layers.append(ft.Container(left=Space.SM, top=Space.SM, content=badge))
    if progress is not None:
        cover_layers.append(
            ft.Container(left=0, right=0, bottom=0, content=ft.ProgressBar(value=progress, bar_height=Size.PROGRESS_BAR, border_radius=0))
        )
    return ft.Card(
        width=240,
        content=ft.Column(
            spacing=0,
            controls=[
                ft.Container(aspect_ratio=16 / 9, content=ft.Stack(expand=True, controls=cover_layers)),
                ft.Container(
                    padding=ft.Padding.only(left=Space.MD, right=Space.MD, top=Space.SM, bottom=Space.MD),
                    content=ft.Column(
                        spacing=Space.XS,
                        controls=[
                            ft.Text(title, style=text_style(TYPE.card_title, color=p.on_surface), max_lines=2,
                                    overflow=ft.TextOverflow.ELLIPSIS),
                            ft.Text("Aoi Motion · 67.2K · 2026-11-14", style=text_style(TYPE.meta, color=p.on_surface_variant),
                                    max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                        ],
                    ),
                ),
            ],
        ),
    )


def cards(p: tk.Palette) -> ft.Control:
    return ft.Row(
        wrap=True,
        spacing=Space.LG,
        run_spacing=Space.LG,
        vertical_alignment=ft.CrossAxisAlignment.START,
        controls=[
            video_card(p, "Starlight Dance 星空のダンス 星光之舞", status_badge(BadgeKind.NEW, p)),
            video_card(p, "Summer Breeze", status_badge(BadgeKind.DOWNLOADING, p, text="42%"), progress=0.42),
            video_card(p, "Neon Night — a much longer title that wraps onto a second line and then ellipsizes", None),
        ],
    )


def list_rows(p: tk.Palette) -> ft.Control:
    return ft.Column(
        spacing=Space.XS,
        controls=[
            ft.ListTile(
                leading=ft.Icon(AppIcon.VIDEO),
                title=ft.Text("Velvet Moon"),
                subtitle=ft.Text("aoi_motion · 25.4 / 48.0 MB · 4.2 MB/s"),
                trailing=ft.Row(tight=True, controls=[status_badge(BadgeKind.QUEUED, p), ft.IconButton(AppIcon.MORE)]),
            ),
            ft.ListTile(
                selected=True,
                leading=ft.Icon(AppIcon.FOLDER_OPEN),
                title=ft.Text(tr("Selected row", "选中行", "選択中の行")),
                subtitle=ft.Text(tr("primary container", "主色容器", "プライマリコンテナ")),
            ),
            ft.Divider(),
            ft.Text(tr("Secondary text uses on-surface-variant.", "次要文字使用 on-surface-variant。",
                       "補足テキストは on-surface-variant。"), style=text_style(TYPE.meta, color=p.on_surface_variant)),
        ],
    )


def icon_grid(p: tk.Palette) -> ft.Control:
    tiles = [
        ft.Container(
            width=132,
            padding=Space.SM,
            border_radius=Radius.CONTROL,
            bgcolor=p.field,
            tooltip=name,
            content=ft.Column(
                spacing=Space.XS,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Icon(icon, size=Size.ICON_LG, color=p.on_surface),
                    ft.Text(name.split(".", 1)[1], size=10, color=p.on_surface_variant, no_wrap=True,
                            overflow=ft.TextOverflow.ELLIPSIS),
                ],
            ),
        )
        for name, icon in all_icons().items()
    ]
    return ft.Row(wrap=True, spacing=Space.SM, run_spacing=Space.SM, controls=tiles)


def rail(selected: int = 0) -> ft.NavigationRail:
    labels = [
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
    return ft.NavigationRail(
        selected_index=selected,
        label_type=ft.NavigationRailLabelType.ALL,
        destinations=[
            ft.NavigationRailDestination(icon=pair.rest, selected_icon=pair.active, label=label)
            for pair, label in labels
        ],
    )


def build(page: ft.Page, mode: str, lang: str) -> ft.ListView:
    app_config.ui_language = LANG_SETTING[lang]
    apply_theme(page, theme_mode=mode, language=lang)
    p = palette_for(page)
    page.controls.clear()

    def set_mode(e: ft.Event[ft.SegmentedButton]):
        build(page, e.control.selected[0], lang)

    def set_lang(e: ft.Event[ft.SegmentedButton]):
        build(page, mode, e.control.selected[0])

    def show_snack(_):
        page.show_dialog(ft.SnackBar(
            ft.Text(tr("3 videos added to the queue", "已加入 3 个视频", "3 本をキューに追加しました")),
            action=tr("Undo", "撤销", "元に戻す"),
        ))

    header = ft.Row(
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[
            ft.Text(tr("Theme gallery", "主题样张", "テーマ見本"), theme_style=ft.TextThemeStyle.HEADLINE_MEDIUM),
            ft.Row(
                spacing=Space.SM,
                controls=[
                    ft.SegmentedButton(
                        selected=[mode], on_change=set_mode,
                        segments=[
                            ft.Segment(value="auto", icon=ft.Icon(AppIcon.THEME_SYSTEM)),
                            ft.Segment(value="light", icon=ft.Icon(AppIcon.THEME_LIGHT)),
                            ft.Segment(value="dark", icon=ft.Icon(AppIcon.THEME_DARK)),
                        ],
                    ),
                    ft.SegmentedButton(
                        selected=[lang], on_change=set_lang,
                        segments=[ft.Segment(value=v, label=ft.Text(v)) for v in ("zh", "ja", "en")],
                    ),
                    ft.OutlinedButton(tr("Snack bar", "提示条", "スナックバー"), icon=AppIcon.INFO, on_click=show_snack),
                ],
            ),
        ],
    )
    body = ft.ListView(
        expand=True,
        spacing=Space.LG,
        padding=ft.Padding.symmetric(horizontal=Space.XXL, vertical=Space.XL),
        controls=[
            header,
            section(tr("Colors", "颜色", "カラー"), swatches(p)),
            section(tr("Type", "字号", "文字"), type_scale(p)),
            section(tr("Buttons", "按钮", "ボタン"), buttons()),
            section(tr("Inputs", "输入", "入力"), inputs()),
            section(tr("Status badges", "状态徽章", "ステータス"), badges(p)),
            section(tr("Video cards", "视频卡片", "動画カード"), cards(p)),
            section(tr("Rows", "列表行", "行"), list_rows(p)),
            section(tr("Icons", "图标", "アイコン"), icon_grid(p)),
        ],
    )
    page.add(ft.Row(expand=True, spacing=0, controls=[rail(), ft.VerticalDivider(width=1), body]))
    page.update()
    return body


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=("auto", "light", "dark"), default="auto")
    parser.add_argument("--lang", choices=("zh", "ja", "en"), default="zh")
    parser.add_argument("--size", default="1440x900", help="window size WxH")
    parser.add_argument("--shot", type=Path, help="save a PNG of the page here and quit")
    parser.add_argument("--scroll", type=int, default=0, help="scroll this many pixels down before the screenshot")
    args = parser.parse_args()
    width, height = (int(v) for v in args.size.lower().split("x"))

    async def app(page: ft.Page):
        page.title = "IwaraTool theme gallery"
        page.padding = 0
        page.window.width, page.window.height = width, height
        page.enable_screenshots = bool(args.shot)
        body = build(page, args.mode, args.lang)
        if args.shot:
            await asyncio.sleep(1.0)
            if args.scroll:
                await body.scroll_to(offset=args.scroll, duration=0)
            await asyncio.sleep(0.8)
            data = await page.take_screenshot(pixel_ratio=1)
            args.shot.parent.mkdir(parents=True, exist_ok=True)
            args.shot.write_bytes(data)
            print(f"saved {args.shot}")
            await page.window.destroy()

    if args.shot:
        args.shot = args.shot.resolve()  # the runner may change the working directory
    run(app)


if __name__ == "__main__":
    main()
