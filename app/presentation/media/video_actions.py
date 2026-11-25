"""The one action list every video's menu shows, in the old unified order (both interfaces).

1. View details
2. Play (labelled by where the copy is) · Open containing folder · Copy file path
3. Download
4. Open in browser · Copy link
5. Author entries
6. Page management entries

``video_actions`` returns the entries; each interface draws them (a context
menu on desktop, an action sheet on phones) and runs them by ``id``.
"""
from __future__ import annotations

from dataclasses import dataclass

from ...core.download.locations import FileLocation, resolve_record_location
from ...i18n import tr

DETAIL = "detail"
PLAY = "play"
OPEN_FOLDER = "open_folder"
COPY_PATH = "copy_path"
NAS_FOLDER = "nas_folder"
COPY_NAS_PATH = "copy_nas_path"
DOWNLOAD = "download"
OPEN_BROWSER = "open_browser"
COPY_LINK = "copy_link"
AUTHOR_PAGE = "author_page"
SELECT = "select"

GROUP_DETAIL, GROUP_PLAY, GROUP_DOWNLOAD, GROUP_LINK, GROUP_AUTHOR, GROUP_PAGE = range(6)


@dataclass(frozen=True, slots=True)
class VideoAction:
    id: str
    label: str
    group: int
    value: str = ""  # the path / url / username the action works on


def record_location(manager, video_id: str) -> FileLocation:
    """Where the downloaded copy of ``video_id`` is (empty when unknown or on error)."""
    if not video_id:
        return FileLocation()
    try:
        return resolve_record_location(manager.history.get_record(video_id))
    except Exception:
        return FileLocation()


def play_label(location: FileLocation, *, can_stream: bool = True) -> str:
    if location.local_path and location.is_remote:
        return tr("Play NAS copy (mounted)", "播放远端文件（挂载）", "リモートのファイルを再生（マウント）")
    if location.local_path:
        return tr("Play local file", "播放本地文件", "ローカルファイルを再生")
    if location.stream_url:
        return tr("Play NAS copy", "播放远端文件", "リモートのファイルを再生")
    if can_stream:
        return tr("Play online", "在线播放", "オンライン再生")
    return ""


def detail_label() -> str:
    return tr("View details", "查看详情", "詳細を表示")


def open_folder_label(*, remote: bool = False) -> str:
    if remote:
        return tr("Open containing folder (NAS)", "打开所在文件夹（远端）", "保存先フォルダーを開く（リモート）")
    return tr("Open containing folder", "打开所在文件夹", "保存先フォルダーを開く")


def copy_path_label(*, remote: bool = False) -> str:
    if remote:
        return tr("Copy NAS path", "复制远端路径", "リモートのパスをコピー")
    return tr("Copy file path", "复制文件路径", "ファイルパスをコピー")


def open_browser_label() -> str:
    return tr("Open in browser", "在浏览器打开", "ブラウザーで開く")


def copy_link_label() -> str:
    return tr("Copy link", "复制链接", "リンクをコピー")


def download_label() -> str:
    return tr("Download", "下载", "ダウンロード")


def author_page_label() -> str:
    return tr("Author page", "作者主页", "作者ページ")


def select_label() -> str:
    return tr("Select", "选择", "選択")


def copied_title() -> str:
    return tr("Copied", "已复制", "コピーしました")


def cannot_open_title() -> str:
    return tr("Cannot open", "无法打开", "開けません")


def video_actions(
    location: FileLocation,
    *,
    url: str = "",
    detail: bool = True,
    can_stream: bool = True,
    download: bool = True,
    author: str = "",
    select: bool = False,
    open_folder: bool = True,
) -> list[VideoAction]:
    """The entries for one video, in menu order (``group`` marks the separators).

    ``open_folder`` is False where no file manager can be opened (phones).
    """

    actions: list[VideoAction] = []
    if detail:
        actions.append(VideoAction(DETAIL, detail_label(), GROUP_DETAIL))
    label = play_label(location, can_stream=can_stream)
    if label:
        actions.append(VideoAction(PLAY, label, GROUP_PLAY, location.playable_target))
    if location.local_path:
        if open_folder:
            actions.append(VideoAction(OPEN_FOLDER, open_folder_label(), GROUP_PLAY))
        actions.append(VideoAction(COPY_PATH, copy_path_label(), GROUP_PLAY, location.local_path))
    elif location.is_remote and location.remote_path:
        if open_folder:
            actions.append(VideoAction(NAS_FOLDER, open_folder_label(remote=True), GROUP_PLAY))
        actions.append(VideoAction(COPY_NAS_PATH, copy_path_label(remote=True), GROUP_PLAY, location.remote_path))
    if download:
        actions.append(VideoAction(DOWNLOAD, download_label(), GROUP_DOWNLOAD))
    url = str(url or "").strip()
    if url:
        actions.append(VideoAction(OPEN_BROWSER, open_browser_label(), GROUP_LINK, url))
        actions.append(VideoAction(COPY_LINK, copy_link_label(), GROUP_LINK, url))
    if author:
        actions.append(VideoAction(AUTHOR_PAGE, author_page_label(), GROUP_AUTHOR, author))
    if select:
        actions.append(VideoAction(SELECT, select_label(), GROUP_PAGE))
    return actions
