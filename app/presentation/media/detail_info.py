"""What a post's detail page shows, from the API answer (both interfaces)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...config import app_config
from ...core import site as sites
from ...core.rating import RATING_ALL, UI_RATING_KEY, filter_by_rating, is_adult, normalize_rating
from ...core.search.models import SearchVideo
from ...core.search.normalize import avatar_url
from ...i18n import current_language, tr
from ..formatting import _format_count, _format_duration
from .detail_text import format_date


@dataclass(frozen=True, slots=True)
class AuthorInfo:
    username: str
    name: str
    user_id: str
    avatar_url: str

    @property
    def target(self) -> tuple[str, str, str, str]:
        """The tuple the author page / subscription flows take."""
        return self.username, self.name, self.user_id, self.avatar_url


@dataclass(frozen=True, slots=True)
class GalleryFile:
    file_id: str
    thumb_url: str
    original_url: str
    aspect: float = 16 / 9  # width / height when the API gives both


def page_url(kind: str, item_id: str, site: str | None = None) -> str:
    return sites.web_url(kind, item_id, site)


def meta_text(video: SearchVideo, kind: str) -> str:
    """``1.2K views · 30 likes · 4 comments · 3:21 · 2026-01-02 · R-18``."""

    parts = [
        tr(f"{_format_count(video.views)} views", f"{_format_count(video.views)} 次播放", f"{_format_count(video.views)} 回再生"),
        tr(f"{_format_count(video.likes)} likes", f"{_format_count(video.likes)} 喜欢", f"{_format_count(video.likes)} いいね"),
        tr(f"{_format_count(video.comments)} comments", f"{_format_count(video.comments)} 评论", f"{_format_count(video.comments)} コメント"),
    ]
    if kind == "video" and video.duration:
        parts.append(_format_duration(video.duration))
    if video.published_at:
        parts.append(format_date(video.published_at))
    if is_adult(video.rating):
        parts.append("R-18")
    return "  ·  ".join(parts)


def like_text(liked: bool, likes: int) -> str:
    count = _format_count(likes)
    return tr(f"Liked · {count}", f"已点赞 · {count}", f"いいね済み · {count}") if liked else tr(f"Like · {count}", f"点赞 · {count}", f"いいね · {count}")


def like_tooltip(liked: bool) -> str:
    if liked:
        return tr("Click to remove your like", "再次点击取消点赞", "もう一度押すと取り消し")
    return tr("Like this post with your Iwara account", "用你的 Iwara 账号点赞", "Iwaraアカウントでいいねする")


def sign_in_to_like() -> tuple[str, str]:
    return tr("Sign in to like", "登录后才能点赞", "いいねにはログインが必要です"), tr(
        "Sign in to Iwara in Settings first.", "请先在设置中登录 Iwara 账号。", "先に設定でIwaraにログインしてください。"
    )


def like_failed_title() -> str:
    return tr("Could not change the like", "点赞操作失败", "いいねを変更できません")


def link_copied_title() -> str:
    return tr("Link copied", "链接已复制", "リンクをコピーしました")


def load_failed_text(error: str) -> str:
    return tr(f"Could not load this post: {error}", f"无法加载该作品：{error}", f"読み込めませんでした: {error}")


def embedded_text() -> str:
    return tr("Embedded from another site — open it in the browser.", "该视频为外站嵌入，请在浏览器中打开。", "外部サイトの埋め込み動画です。ブラウザーで開いてください。")


def gallery_hint(count: int) -> str:
    if count > 1:
        return tr(f"{count} images — click one to open the original", f"共 {count} 张图片，点击图片打开原图", f"{count} 枚 — クリックで原寸を開く")
    if count:
        return tr("Click the image to open the original", "点击图片打开原图", "クリックで原寸を開く")
    return ""


def file_quality(info: dict[str, Any]) -> str:
    file_info = info.get("file") if isinstance(info.get("file"), dict) else {}
    return f"{file_info['height']}p" if file_info.get("height") else ""


def author_of(info: dict[str, Any], video: SearchVideo | None = None) -> AuthorInfo | None:
    user = info.get("user") if isinstance(info.get("user"), dict) else {}
    username = str(user.get("username") or (video.author_username if video else "") or "")
    if not username:
        return None
    return AuthorInfo(username, str(user.get("name") or username), str(user.get("id") or ""), avatar_url(user))


def tag_chips(tags: object, tag_dictionary: Any = None) -> list[tuple[str, str]]:
    """``(tag id, label)`` in the UI language, skipping "uncategorized"."""

    chips = []
    language = current_language()
    for tag in tags if isinstance(tags, list) else []:
        tag_id = str(tag.get("id") if isinstance(tag, dict) else tag or "").strip()
        if not tag_id or tag_id == "uncategorized":
            continue
        label = tag_id
        try:
            entry = tag_dictionary.entry_for(tag_id) if tag_dictionary is not None else None
        except Exception:
            entry = None
        if entry is not None:
            label = getattr(entry, language, "") or tag_id
        chips.append((tag_id, f"#{label}"))
    return chips


def gallery_file(file_info: dict[str, Any]) -> GalleryFile:
    file_id = str(file_info.get("id") or "")
    name = str(file_info.get("name") or "")
    try:
        width, height = float(file_info.get("width") or 0), float(file_info.get("height") or 0)
    except (TypeError, ValueError):
        width = height = 0.0
    aspect = min(max(width / height, 0.3), 4.0) if width > 0 and height > 0 else 16 / 9
    return GalleryFile(
        file_id=file_id,
        thumb_url=f"https://i.iwara.tv/image/thumbnail/{file_id}/{file_id}.jpg",
        original_url=f"https://i.iwara.tv/image/original/{file_id}/{name}" if name else "",
        aspect=aspect,
    )


def gallery_files(info: dict[str, Any]) -> list[dict[str, Any]]:
    files = [f for f in info.get("files") or [] if isinstance(f, dict) and f.get("id")]
    if not files and isinstance(info.get("thumbnail"), dict):
        files = [info["thumbnail"]]
    return files


def rated_related(videos: list[SearchVideo]) -> list[SearchVideo]:
    """The related endpoint ignores the SFW/NSFW choice; honour it here."""
    return filter_by_rating(videos, normalize_rating(app_config.get_ui_value(UI_RATING_KEY, RATING_ALL)))


def tag_search_request(kind: str, tag_id: str) -> dict[str, str]:
    return {"scope": "images" if kind == "image" else "tags", "keyword": tag_id}
