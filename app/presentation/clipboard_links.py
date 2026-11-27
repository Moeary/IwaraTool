"""A copied Iwara link, offered when a phone app comes back to the foreground.

Flet 1.1.3 cannot receive Android share intents,
so the phone build looks at the clipboard instead: copy a link in the browser,
switch to IwaraTool, and it offers to open it.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from ..core.site import is_iwara_url
from ..i18n import tr
from .downloads.submitted_urls import split_submitted_urls

KINDS = {"video": "video", "videos": "video", "image": "image", "images": "image", "profile": "profile"}


@dataclass(frozen=True)
class CopiedLink:
    kind: str  # video / image / profile
    key: str  # the video or image id, or the username
    url: str


def copied_link(text: str) -> CopiedLink | None:
    """The first Iwara work or profile link in ``text``."""
    for url in split_submitted_urls(text):
        if not url.lower().startswith(("http://", "https://")) or not is_iwara_url(url):
            continue
        try:
            parts = [p for p in urlparse(url).path.split("/") if p]
        except ValueError:
            continue
        if len(parts) >= 2 and parts[0].lower() in KINDS and parts[1]:
            return CopiedLink(KINDS[parts[0].lower()], parts[1], url)
    return None


def offer_title(link: CopiedLink) -> str:
    if link.kind == "profile":
        return tr("Copied author link", "已复制作者链接", "作者リンクをコピー済み")
    return tr("Copied Iwara link", "已复制 Iwara 链接", "Iwara リンクをコピー済み")


def open_label() -> str:
    return tr("Open", "打开", "開く")
