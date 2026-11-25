"""What the search controls offer: sources, scopes, sorts, hints and the result summary.

Shared by both interfaces; the wording and the rules about which endpoint
accepts which order live here, the widgets only show them.
"""
from __future__ import annotations

from ...core import site as sites
from ...core.search.query_params import playlist_reference, profile_reference
from ...i18n import tr

SOURCE_IWARA = "iwara"
SOURCE_ORENO3D = "oreno3d"


def source_label(source: str, *, short: bool = False) -> str:
    if source == SOURCE_ORENO3D:
        return "Oreno3D" if short else tr("Oreno3D online search", "Oreno3D 在线搜索", "Oreno3Dオンライン検索")
    return "Iwara" if short else tr("Iwara live API", "Iwara 实时 API", "IwaraライブAPI")


def oreno3d_available() -> bool:
    """Oreno3D indexes iwara.tv only."""
    return sites.configured_site() != sites.SITE_AI


def source_tooltip() -> str:
    if not oreno3d_available():
        return tr("Search source (Oreno3D only covers iwara.tv)", "数据源（Oreno3D 只收录 iwara.tv）", "検索ソース（Oreno3D は iwara.tv のみ）")
    return tr("Search source", "数据源", "検索ソース")


def scope_items() -> list[tuple[str, str]]:
    return [
        (tr("Videos", "视频", "動画"), "videos"),
        (tr("Images", "图片", "画像"), "images"),
        (tr("Authors", "作者", "作者"), "authors"),
        (tr("Tags", "标签搜索", "タグ検索"), "tags"),
        (tr("Playlists", "播放列表", "プレイリスト"), "playlists"),
    ]


def supported_scopes(source: str) -> set[str]:
    """Scopes the online source can answer."""
    if source == SOURCE_ORENO3D:
        return {"videos", "tags"}
    return {"videos", "images", "authors", "tags", "playlists"}


def sort_choices(source: str, scope: str, keyword: str) -> list[tuple[str, str]]:
    """Only the orders the selected endpoint accepts."""

    iwara = source == SOURCE_IWARA
    keyword = str(keyword or "").strip()
    newest = (tr("Newest", "最新", "新着"), "date")
    relevance = (tr("Relevance", "相关度", "関連度"), "relevance")
    views = (tr("Most viewed", "播放最多", "再生数順"), "views")
    likes = (tr("Most liked", "点赞最多", "いいね順"), "likes")
    if iwara and scope in {"images", "videos"} and keyword:
        return [newest, relevance, views, likes]  # native /search for videos and images
    if iwara and (
        (scope == "authors" and not profile_reference(keyword))
        or (scope == "playlists" and not playlist_reference(keyword))
    ):
        return [newest, relevance]  # native user and playlist search
    return [
        newest,
        (tr("Trending", "趋势", "トレンド"), "trending"),
        (tr("Popularity", "热度", "人気"), "popularity"),
        views,
        likes,
    ]


def keyword_placeholder(scope: str) -> str:
    if scope == "authors":
        return tr("Author name, @username or profile URL…", "输入作者名称、@用户名或主页链接…", "作者名、@ユーザー名、プロフィールURL…")
    if scope == "images":
        return tr("Image keywords (blank to browse)…", "输入图片关键词（留空浏览）…", "画像のキーワード（空欄で一覧）…")
    if scope == "tags":
        return tr("Type a tag, select a candidate, then add another…", "输入标签，选择候选后可继续添加…", "タグを入力し、候補選択後に続けて追加…")
    if scope == "playlists":
        return tr("Playlist keywords, ID or URL…", "输入播放列表关键词、ID 或链接…", "プレイリストのキーワード、ID、URL…")
    return tr("Keywords or titles…", "输入关键词或标题…", "キーワードまたはタイトル…")


def scope_hint(scope: str, source: str) -> str:
    """What the keyword box does for ``scope`` (a tooltip or a line under it)."""

    if scope == "authors":
        return tr(
            "Searches Iwara users by name. Use @username or a profile URL to open one exact account.",
            "按名称搜索 Iwara 用户；输入 @用户名 或个人主页链接可直接定位到该账号。",
            "名前でIwaraユーザーを検索します。@ユーザー名またはプロフィールURLで特定のアカウントを開けます。",
        )
    if scope == "images":
        return tr(
            "Searches Iwara image posts; leave blank to browse them. Images can be viewed, but not queued for download.",
            "搜索 Iwara 图片作品，留空则浏览图片列表；图片可查看，但不能加入下载队列。",
            "Iwaraの画像投稿を検索します。空欄なら一覧を表示します。閲覧はできますが、ダウンロードキューには追加できません。",
        )
    if scope == "tags":
        if source == SOURCE_ORENO3D:
            return tr(
                "Mapped Iwara labels use typed numeric Oreno3D routes; unknown names fall back to keyword search. Multiple tags are matched by intersection. tag:<id>, origin:<id>, and character:<id> are also supported.",
                "已匹配的 Iwara 标签会自动转为对应的 Oreno3D 数字路由；未知名称回退到关键词搜索。多个标签会取交集；也支持 tag:<id>、origin:<id>、character:<id>。",
                "対応するIwaraラベルはOreno3Dの型付き数値ルートに変換し、未知名はキーワード検索に戻します。複数タグは共通結果を求めます。tag:<id>・origin:<id>・character:<id>にも対応します。",
            )
        return tr(
            "Search for all specified tags. Select suggestions or enter exact translated names separated by commas; names are converted to Iwara tag IDs.",
            "查找同时包含所选标签的视频。可选择候选或输入完整译名，用逗号分隔；提交时会转换为 Iwara 标签 ID。",
            "指定したすべてのタグを含む動画を検索します。候補または正確な翻訳名をカンマで区切って入力すると、IwaraタグIDに変換します。",
        )
    if scope == "playlists":
        return tr(
            "Searches playlists by title. Paste a playlist ID or iwara.tv/playlist/... URL to list its videos; double-click a playlist card to open it here.",
            "按标题搜索播放列表；粘贴播放列表 ID 或 iwara.tv/playlist/... 链接可直接列出其中视频，双击播放列表卡片也可在此打开。",
            "タイトルでプレイリストを検索します。プレイリストIDまたはiwara.tv/playlist/... URLで動画一覧を表示し、カードをダブルクリックしてもここで開けます。",
        )
    if source == SOURCE_IWARA:
        return tr(
            "Search text across Iwara. Keywords and tags are separate; use double quotes for an exact phrase. Leave blank to browse videos.",
            "在 Iwara 中搜索关键词，与标签搜索分开；精确短语可加双引号。留空则浏览视频列表。",
            "Iwara内のキーワード検索です。タグ検索とは別です。完全一致の語句は二重引用符で囲み、空欄なら動画一覧を表示します。",
        )
    return tr(
        "Oreno3D is only a search bridge. Opening a result resolves and shows the final Iwara page.",
        "Oreno3D 仅作为搜索桥接；打开结果时会解析并展示最终的 Iwara 页面。",
        "Oreno3Dは検索ブリッジのみです。結果を開くとIwaraのページを表示します。",
    )


def searching_text() -> str:
    return tr("Searching…", "搜索中…", "検索中…")


def enter_terms_text() -> str:
    return tr("Enter search terms", "请输入搜索条件", "検索条件を入力してください")


def failed_text(error: str) -> str:
    return tr(f"Search failed: {error}", f"搜索失败：{error}", f"検索失敗: {error}")


def found_text(scope: str, *, videos: int, authors: int, playlists: int, total: int | None, shown: int | None = None) -> str:
    """``Found 22 videos / 300 · 18 after filtering``."""

    suffix = f" / {total}" if total is not None else ""
    if scope == "authors":
        return tr(f"Found {authors} author(s){suffix}", f"找到 {authors} 位作者{suffix}", f"作者 {authors} 件{suffix}")
    if playlists:
        return tr(f"Found {playlists} playlist(s){suffix}", f"找到 {playlists} 个播放列表{suffix}", f"プレイリスト {playlists} 件{suffix}")
    filtered = tr(f" · {shown} after filtering", f" · 筛选后 {shown} 个", f" · 絞り込み後 {shown} 件") if shown is not None else ""
    if scope == "images":
        return tr(f"Found {videos} image post(s){suffix}", f"找到 {videos} 个图片作品{suffix}", f"画像 {videos} 件{suffix}") + filtered
    return tr(f"Found {videos} video(s){suffix}", f"找到 {videos} 个视频{suffix}", f"動画 {videos} 件{suffix}") + filtered


def keyword_route(keyword: str, scope: str, source: str, *, pinned: bool, auto_routed: bool) -> tuple[str | None, bool]:
    """Chinese video keywords go to Oreno3D; the next other search goes back.

    Returns ``(source to switch to or None, auto_routed afterwards)``.
    """

    from ...config import app_config
    from .keyword_language import is_chinese_keyword

    if pinned or not oreno3d_available() or scope != "videos":
        return None, auto_routed
    chinese = bool(app_config.search_chinese_via_oreno3d) and is_chinese_keyword(keyword)
    if chinese and source == SOURCE_IWARA:
        return SOURCE_ORENO3D, True
    if not chinese and auto_routed:
        default = str(app_config.search_default_source or SOURCE_ORENO3D)
        return (default if default != source else None), False
    return None, auto_routed


def routed_text() -> tuple[str, str]:
    return tr("Searching Oreno3D", "已改用 Oreno3D 搜索", "Oreno3D で検索"), tr(
        "Chinese keywords are searched with Oreno3D (see Settings › Search).",
        "中文关键词使用 Oreno3D 搜索（可在 设置 › 搜索 中关闭）。",
        "中国語キーワードは Oreno3D で検索します（設定 › 検索）。",
    )
