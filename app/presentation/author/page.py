"""The author page's tabs and texts (both interfaces)."""
from __future__ import annotations

from ...core.home.feed import MODE_AUTHOR, FeedSection, FeedTab
from ...i18n import tr


def author_section(username: str, name: str = "") -> FeedSection:
    """Videos and images by ``username``, newest first (``load_feed_page`` reads them)."""
    return FeedSection(
        "author",
        name or username,
        (
            FeedTab("videos", tr("Videos", "视频", "動画"), "video", (("sort", "date"),), mode=MODE_AUTHOR, value=username),
            FeedTab("images", tr("Images", "图片", "画像"), "image", (("sort", "date"),), mode=MODE_AUTHOR, value=username),
        ),
    )


def open_in_search_text() -> str:
    return tr("Open in Search", "在搜索页打开", "検索ページで開く")


def open_on_site_text() -> str:
    return tr("Open on Iwara", "在 Iwara 打开", "Iwaraで開く")


def nothing_to_download_notice() -> tuple[str, str]:
    return tr("Nothing to download", "没有可下载的内容", "ダウンロード対象がありません"), tr(
        "Image posts and embedded videos cannot be queued.", "图片作品和外站嵌入视频无法加入下载队列。",
        "画像投稿と埋め込み動画はキューに追加できません。")
