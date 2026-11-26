"""Settings groups of the new interface, by what a setting is for (maintainer, 2026-11-26).

The Classic page keeps ``catalog.CATEGORIES``; the new page shows these
groups, built from the same card keys.  "About" holds the version, author,
licence, links and update checks.  Old links (a Classic category or a card
name) resolve through ``ALIASES``.
"""
from __future__ import annotations

from dataclasses import dataclass

from ...i18n import tr

GROUP_KEY = "settings_group_v2"  # the group shown last


@dataclass(frozen=True)
class Group:
    key: str
    title: str
    summary: str
    cards: tuple[str, ...]
    desktop_only: bool = False


def groups() -> tuple[Group, ...]:
    """Every group in order; built on demand so the texts follow the language.

    Card keys are the shared ones, plus the new page's merged cards (``MERGED``).
    """
    return (
        Group("appearance", tr("Appearance", "外观", "外観"),
              tr("Theme, language and font", "主题、语言与字体", "テーマ・言語・フォント"),
              ("look",)),
        Group("browsing", tr("Browsing", "浏览", "閲覧"),
              tr("Site, content rating, the Home rows, clicks on covers and playback",
                 "站点、内容分级、首页栏目、点击封面的动作与播放",
                 "サイト・コンテンツ区分・ホームの欄・カバーのクリック・再生"),
              ("source", "home_layout", "card_clicks", "playback")),
        Group("account", tr("Account and network", "账号与网络", "アカウントとネットワーク"),
              tr("Iwara sign-in, proxy and request pacing", "Iwara 登录、代理与请求节奏", "Iwara ログイン・プロキシ・リクエスト間隔"),
              ("account", "proxy", "request_policy")),
        Group("downloads", tr("Downloads", "下载", "ダウンロード"),
              tr("Quality, what a download saves, concurrency and aria2", "画质、下载内容、并发与 aria2",
                 "画質・保存内容・同時実行・aria2"),
              ("quality", "behavior", "concurrency", "download_policy", "aria2")),
        Group("storage", tr("Storage", "存储", "ストレージ"),
              tr("Download folder, data folder, caches, backup and logs", "下载目录、数据目录、缓存、备份与日志",
                 "保存先・データフォルダー・キャッシュ・バックアップとログ"),
              ("download_dir", "data_paths", "home_cache", "maintenance")),
        Group("search", tr("Search", "搜索", "検索"),
              tr("Search engine, history, limits and the tag dictionary", "搜索方式、历史、数量上限与标签词典",
                 "検索方式・履歴・上限・タグ辞書"),
              ("search_engine", "search_history", "search_limit", "search_bridge")),
        Group("subscriptions", tr("Subscriptions", "订阅", "購読"),
              tr("Automatic refresh and subscribe prompts", "自动刷新与订阅提示", "自動更新と購読の確認"),
              ("subscription_automation", "subscription_prompt")),
        Group("system", tr("System", "系统", "システム"),
              tr("Window and startup, FFmpeg and how hard covers and refreshes work",
                 "窗口与启动、FFmpeg、封面加载与刷新的并发",
                 "ウィンドウと起動・FFmpeg・カバー読み込みと更新の同時実行"),
              ("window", "ffmpeg", "cover_performance")),
        Group("shortcuts", tr("Keyboard shortcuts", "键盘快捷键", "キーボードショートカット"),
              tr("Shortcuts for every page and the app", "各页面与全局的快捷键", "各ページと全体のショートカット"),
              ("shortcuts",), desktop_only=True),
        Group("about", tr("About", "关于", "情報"),
              tr("Version, author, licence and updates", "版本、作者、许可与更新", "バージョン・作者・ライセンス・更新"),
              ("about",)),
    )


# The new page merges small related cards into one (no title repeated as the row below it).
MERGED = {
    "look": ("appearance", "language", "font"),
    "source": ("site", "rating"),
    "about": ("about", "updates"),
}


def shared_cards(group: Group) -> tuple[str, ...]:
    """A group's cards as the shared (Classic) card keys, merged cards expanded."""
    return tuple(card for key in group.cards for card in MERGED.get(key, (key,)))


# Classic categories and card names → the group that holds them now.
ALIASES = {
    "general": "appearance", "home": "browsing", "network": "account", "tools": "system",
    **{card: g.key for g in groups() for card in shared_cards(g) if card != g.key},
}


def visible(*, desktop: bool) -> tuple[Group, ...]:
    return tuple(g for g in groups() if desktop or not g.desktop_only)


def find(key: str, *, desktop: bool = True) -> Group | None:
    key = ALIASES.get(key, key)
    return next((g for g in visible(desktop=desktop) if g.key == key), None)


REPOSITORY_URL = "https://github.com/Moeary/IwaraTool"
RELEASES_URL = REPOSITORY_URL + "/releases"
AUTHOR = "Moeary"
LICENCE = "MIT"


def about_lines() -> list[tuple[str, str]]:
    """``(label, value)`` rows of the About card (the version is added by the page)."""
    return [
        (tr("Author", "作者", "作者"), AUTHOR),
        (tr("Licence", "许可", "ライセンス"), LICENCE),
        (tr("Source code", "源代码", "ソースコード"), REPOSITORY_URL),
    ]


def about_texts() -> dict[str, str]:
    return {
        "title": tr("About IwaraTool", "关于 IwaraTool", "IwaraTool について"),
        "version": tr("Version", "版本", "バージョン"),
        "tagline": tr("Browse, download and organize Iwara videos.", "浏览、下载与整理 Iwara 视频。",
                      "Iwara の動画を閲覧・保存・整理します。"),
        "repository": tr("GitHub", "GitHub 仓库", "GitHub"),
        "releases": tr("Releases", "版本发布页", "リリース"),
        "issues": tr("Report a problem", "反馈问题", "問題を報告"),
        "rating_title": tr("Content rating", "内容分级", "コンテンツ区分"),
        "rating_help": tr("Which works Home, Search and author pages show. The button at the top of the sidebar shows and switches it too.",
                          "首页、搜索与作者页显示哪些作品。侧栏顶部的按钮也显示并切换当前分级。",
                          "ホーム・検索・作者ページに表示する作品。サイドバー上部のボタンでも確認・切り替えできます。"),
    }
