"""What a search request asks for and how its pages add up, without a UI framework."""
from __future__ import annotations

from dataclasses import dataclass, field

from ...core.search.models import SearchAuthor, SearchPageResult, SearchPlaylist, SearchVideo
from ...i18n import tr

SCOPES = ("videos", "images", "authors", "tags", "playlists")
SOURCES = ("iwara", "oreno3d")
PROBLEM_WARNING = "warning"
PROBLEM_ERROR = "error"


def page_size(iwara: bool, rating: str, keyword: str) -> int:
    if rating and keyword:
        # /search ignores the rating, so the page is filtered locally;
        # ask for the largest page so a filtered page is not nearly empty.
        return 100
    return 32 if iwara else 36


def request_problem(scope: str, source: str, keyword: str) -> tuple[str, str] | None:
    """``(level, message)`` when the request cannot run as asked, else ``None``."""

    if scope in {"images", "authors", "playlists"} and source == "oreno3d":
        return PROBLEM_WARNING, tr(
            "Oreno3D online search currently returns video cards. Switch to Iwara live API for image, author or playlist results.",
            "Oreno3D 在线搜索当前返回视频卡片；图片、作者或播放列表结果请切换到 Iwara 实时 API。",
            "Oreno3Dオンライン検索は現在動画カードを返します。画像・作者・プレイリストはIwaraライブAPIへ切り替えてください。",
        )
    if keyword:
        return None
    if scope == "authors":
        return PROBLEM_ERROR, tr("Enter an author name first", "请先输入作者名称", "作者名を入力してください")
    if scope == "tags":
        return PROBLEM_ERROR, tr("Enter at least one tag", "请至少输入一个标签", "タグを1つ以上入力してください")
    if scope == "playlists":
        return PROBLEM_ERROR, tr(
            "Enter playlist keywords, an ID or a URL", "请输入播放列表关键词、ID 或链接", "プレイリストのキーワード、ID、URLを入力してください"
        )
    return None


@dataclass
class ResultSet:
    """Results of one search session across the pages read so far (no duplicates)."""

    videos: list[SearchVideo] = field(default_factory=list)
    authors: list[SearchAuthor] = field(default_factory=list)
    playlists: list[SearchPlaylist] = field(default_factory=list)
    total: int | None = None
    current_page: int = 0
    last_page: int | None = None
    next_page: int | None = 0

    def clear(self) -> None:
        self.videos.clear()
        self.authors.clear()
        self.playlists.clear()
        self.total, self.current_page, self.last_page, self.next_page = None, 0, None, 0

    def add(self, result: SearchPageResult, *, replace: bool) -> None:
        merge_page(self.videos, self.authors, self.playlists, result, replace=replace)
        self.total = result.total
        self.current_page = max(0, int(result.current_page or 0))
        self.last_page = result.last_page
        self.next_page = result.next_page


def merge_page(videos: list, authors: list, playlists: list, result: SearchPageResult, *, replace: bool) -> None:
    """Add one page to the session's lists in place, skipping items already shown."""

    if replace:
        videos.clear()
        authors.clear()
        playlists.clear()
    _extend_unique(playlists, result.playlists, lambda item: item.playlist_id)
    _extend_unique(videos, result.videos, lambda item: item.video_id)
    _extend_unique(authors, result.authors, lambda item: item.author_id)


def _extend_unique(target: list, items, key) -> None:
    seen = {key(item) for item in target}
    for item in items:
        if key(item) not in seen:
            target.append(item)
            seen.add(key(item))
