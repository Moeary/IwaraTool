"""One Home row's state and decisions, without a UI framework.

The row paints from the on-disk cache first.  A background re-check runs only
when the cached copy is older than the "refresh after" setting (or the user
presses refresh), a failed check is not retried for a while, and the cards are
replaced only when the posts changed.  New posts that arrive while cards are
selected wait until the selection is released.

The interface owns the widgets and the worker; it asks ``plan_reload`` what to
do, calls ``begin_fetch`` before starting a load and ``on_result`` when it
lands, and draws what those return.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from ...config import app_config
from ...core.home.cache import DEFAULT_CACHE_MINUTES, HOME_CACHE_MINUTES_KEY, HomeFeedCache, cache_key, rows_signature
from ...core.home.feed import FeedSection, FeedTab
from ...core.home.loading import FeedResult
from ...core.rating import RATING_ALL, normalize_rating
from ...core.search.models import SearchVideo
from ...i18n import tr
from ..formatting import format_age

RETRY_AFTER_SECONDS = 120  # wait this long before re-trying a failed background check

# ``on_result`` outcomes
IGNORE = "ignore"  # an answer to an older request
STASHED = "stashed"  # fetched for another site / rating: cached for later, not shown
ERROR_KEPT = "error_kept"  # the check failed; the cards on screen stay
ERROR = "error"  # nothing to show and the load failed: offer a retry
UNCHANGED = "unchanged"  # same posts: leave the cards alone
PENDING = "pending"  # new posts wait for the selection to be released
SHOW = "show"  # draw these rows


def queueable_ids(videos: list[SearchVideo]) -> list[str]:
    """Iwara video ids that can be downloaded (image posts and embeds cannot)."""

    seen: set[str] = set()
    ids: list[str] = []
    for video in videos:
        if video.source_kind == "iwara" and video.downloadable and video.video_id not in seen:
            seen.add(video.video_id)
            ids.append(video.video_id)
    return ids


def cache_ttl_seconds() -> float:
    """How long a cached row counts as fresh; 0 means "only refresh when asked"."""

    try:
        return max(0, int(app_config.get_ui_value(HOME_CACHE_MINUTES_KEY, DEFAULT_CACHE_MINUTES))) * 60.0
    except (TypeError, ValueError):
        return DEFAULT_CACHE_MINUTES * 60.0


@dataclass(slots=True)
class ReloadPlan:
    login_required: bool = False  # the account feed while signed out
    clear: bool = False  # drop the cards on screen first
    show: tuple[list[dict], float, str] | None = None  # (rows, saved_at, key) to draw now
    fetch: bool = False  # start a load (``begin_fetch``)
    keep_cards: bool = False  # while fetching: cards are on screen, so no skeleton


@dataclass(slots=True)
class ResultOutcome:
    action: str
    rows: list[dict] | None = None
    error: str = ""


class SectionFeed:
    def __init__(self, section: FeedSection, cache: HomeFeedCache):
        self.section = section
        self.cache = cache
        self.rating = RATING_ALL
        self.token = 0
        self.fetch_key = ""
        self.shown_key = ""
        self.shown_signature: tuple[str, ...] | None = None
        self.saved_at = 0.0  # epoch seconds of the data on screen
        self.last_attempt = 0.0  # monotonic, throttles failed background checks
        self.pending: FeedResult | None = None
        saved = str(app_config.get_ui_value(self._tab_key, "") or "")
        self.tab_id = section.tab(saved).id
        self.collapsed = str(app_config.get_ui_value(self._collapsed_key, "0")) in {"1", "true", "True"}

    # ── settings ─────────────────────────────────────────────────────────────

    @property
    def _tab_key(self) -> str:
        return f"home_tab_{self.section.id}_v1"

    @property
    def _collapsed_key(self) -> str:
        return f"home_collapsed_{self.section.id}_v1"

    @property
    def tab(self) -> FeedTab:
        return self.section.tab(self.tab_id)

    def select_tab(self, tab_id: str) -> bool:
        """True when the tab changed (then reload)."""
        if tab_id == self.tab_id:
            return False
        self.tab_id = tab_id
        app_config.set_ui_value(self._tab_key, tab_id)
        self.shown_key = ""
        return True

    def set_collapsed(self, collapsed: bool) -> None:
        self.collapsed = bool(collapsed)
        app_config.set_ui_value(self._collapsed_key, "1" if self.collapsed else "0")

    def set_rating(self, rating: str) -> None:
        self.rating = normalize_rating(rating)

    def invalidate(self) -> None:
        """Allow the next show to check again at once."""
        self.last_attempt = 0.0

    def cache_key(self) -> str:
        return cache_key(self.section.id, self.tab.id, self.rating, account=self.tab.needs_login)

    def is_stale(self) -> bool:
        ttl = cache_ttl_seconds()
        if ttl <= 0:
            return not self.saved_at
        return not self.saved_at or time.time() - self.saved_at > ttl

    # ── loading ──────────────────────────────────────────────────────────────

    def clear_shown(self) -> None:
        self.shown_key, self.shown_signature, self.saved_at = "", None, 0.0

    def mark_shown(self, rows: list[dict], saved_at: float, key: str) -> None:
        self.pending = None
        self.shown_key = key
        self.shown_signature = rows_signature(rows)
        self.saved_at = saved_at

    def plan_reload(self, *, force: bool = False, logged_in: bool = True, has_items: bool = False) -> ReloadPlan:
        """What showing the row needs now; ``has_items``: cards are on screen."""

        if self.tab.needs_login and not logged_in:
            return ReloadPlan(login_required=True, clear=True)
        if self.collapsed:
            return ReloadPlan()  # nothing to show or fetch until it is expanded
        plan = ReloadPlan()
        key = self.cache_key()
        entry = self.cache.get(key)
        if entry is None and self.shown_key and key != self.shown_key:
            # Another site or rating with nothing cached: do not keep showing the old cards.
            plan.clear = True
            has_items = False
        if entry is not None and (key != self.shown_key or not has_items):
            plan.show = (entry.rows, entry.saved_at, key)
        elif entry is not None:
            self.saved_at = entry.saved_at
        if entry is not None and not force:
            ttl = cache_ttl_seconds()
            if ttl <= 0 or entry.is_fresh(ttl):
                return plan
            if self.last_attempt and time.monotonic() - self.last_attempt < RETRY_AFTER_SECONDS:
                return plan
        plan.fetch = True
        plan.keep_cards = entry is not None or has_items
        return plan

    def begin_fetch(self) -> tuple[int, FeedTab, dict[str, str]]:
        """``(token, tab, request params)`` for the load about to start."""
        self.token += 1
        self.fetch_key = self.cache_key()
        self.last_attempt = time.monotonic()
        tab = self.tab
        return self.token, tab, tab.request_params(self.rating)

    def on_result(self, result: FeedResult, *, has_items: bool, has_selection: bool) -> ResultOutcome:
        if result.token != self.token:
            return ResultOutcome(IGNORE)
        key = self.fetch_key or self.cache_key()
        if key != self.cache_key():
            if not result.error:
                self.cache.put(key, result.rows)
            return ResultOutcome(STASHED)
        if result.error and not result.items:
            return ResultOutcome(ERROR_KEPT if has_items else ERROR, error=result.error)
        changed = self.cache.put(key, result.rows)
        if key == self.shown_key and not changed and has_items:
            self.saved_at = time.time()
            return ResultOutcome(UNCHANGED)
        if changed and has_items and has_selection:
            self.pending = result
            return ResultOutcome(PENDING)
        return ResultOutcome(SHOW, rows=result.rows)

    def take_pending(self) -> list[dict] | None:
        pending, self.pending = self.pending, None
        return pending.rows if pending is not None else None

    # ── words ────────────────────────────────────────────────────────────────

    def age_text(self) -> str:
        if not self.saved_at:
            return ""
        text = format_age(time.time() - self.saved_at)
        return tr(f"Updated {text}", f"更新于{text}", f"{text}に更新")

    def more_text(self) -> str:
        return tr("More in Search ›", "在搜索页查看更多 ›", "検索ページで見る ›") if self.tab.opens_in_search else tr("More ›", "查看更多 ›", "もっと見る ›")


def login_prompt() -> str:
    return tr(
        "Sign in to see the newest uploads from the creators you follow on Iwara.",
        "登录 Iwara 账号后，这里会显示你关注作者的最新作品。",
        "Iwaraにログインすると、フォロー中の作者の最新作がここに表示されます。",
    )


def login_button_text() -> str:
    return tr("Open Settings to sign in", "前往设置登录", "設定でログイン")


def empty_text() -> str:
    return tr("Nothing here yet.", "这里暂时没有内容。", "まだ何もありません。")


def loading_text() -> str:
    return tr("Loading…", "加载中…", "読み込み中…")


def load_failed_text(error: str) -> str:
    return tr(f"Could not load: {error}", f"加载失败：{error}", f"読み込めませんでした: {error}")


def refresh_failed_text() -> str:
    return tr("Couldn't refresh", "刷新失败", "更新に失敗")


def new_posts_text() -> str:
    return tr("New posts — show", "有新内容，点击显示", "新着あり — 表示")


def retry_text() -> str:
    return tr("Try again", "重试", "再試行")


def refresh_tooltip(busy: bool) -> str:
    return tr("Refreshing…", "正在刷新…", "更新中…") if busy else tr("Refresh this row now", "立即刷新这一行", "この行を今すぐ更新")


def queue_text(selected: int, count: int) -> str:
    if selected:
        return tr(f"Download selected ({count})", f"下载选中（{count}）", f"選択をダウンロード（{count}）")
    return tr("Download shown", "下载当前展示", "表示中をダウンロード")


def queue_tooltip() -> str:
    return tr("Queue every video shown in this row", "把这一行展示的视频全部加入下载队列", "この行の動画をすべてキューに追加")


def fold_tooltip(collapsed: bool) -> str:
    return tr("Click to expand this row", "点击展开这一行", "クリックで展開") if collapsed else tr("Click to fold this row", "点击折叠这一行", "クリックで折りたたむ")


def items_for(feed: SectionFeed, rows: list[dict]) -> list[SearchVideo]:
    from ...core.home.loading import items_from_rows

    tab = feed.tab
    return items_from_rows(tab.kind, rows, tab.mode, feed.rating)


