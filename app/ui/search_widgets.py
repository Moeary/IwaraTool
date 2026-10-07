"""Reusable widgets and presentation helpers for the search page."""
from __future__ import annotations

import json
import re
from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QAbstractItemView,
    QListWidgetItem,
    QStyle,
    QStyleOptionViewItem,
    QWidget,
)

from qfluentwidgets import LineEdit, ListWidget

from ..core.search import SearchAuthor, SearchVideo
from ..core.tag_dictionary import TagSuggestion
from ..i18n import tr
from .theme import palette, popup_list_qss, scrollbar_qss


DEFAULT_SEARCH_HISTORY_LIMIT = 20
MAX_SEARCH_HISTORY_LIMIT = 100


def _normalize_search_history_entry(value: object) -> dict[str, str] | None:
    """Keep one persisted search-history item small, valid, and portable."""

    if not isinstance(value, dict):
        return None
    entry = {
        "keyword": str(value.get("keyword") or "").strip(),
        "source": str(value.get("source") or "oreno3d").strip().lower(),
        "scope": str(value.get("scope") or "videos").strip().lower(),
        "sort": str(value.get("sort") or "date").strip().lower(),
    }
    if entry["source"] not in {"oreno3d", "iwara"}:
        entry["source"] = "oreno3d"
    if entry["scope"] not in {"videos", "authors", "tags", "playlists"}:
        entry["scope"] = "videos"
    if not entry["keyword"] and entry["scope"] != "videos":
        return None
    return entry


def _upsert_search_history(
    history: object,
    entry: object,
    limit: int = DEFAULT_SEARCH_HISTORY_LIMIT,
) -> list[dict[str, str]]:
    """Return an MRU history list with duplicate queries collapsed."""

    normalized_entry = _normalize_search_history_entry(entry)
    if normalized_entry is None:
        return []
    try:
        safe_limit = max(1, min(MAX_SEARCH_HISTORY_LIMIT, int(limit)))
    except (TypeError, ValueError):
        safe_limit = DEFAULT_SEARCH_HISTORY_LIMIT

    values = history if isinstance(history, list) else []
    normalized: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for value in [normalized_entry, *values]:
        item = _normalize_search_history_entry(value)
        if item is None:
            continue
        key = tuple(item[field].casefold() for field in ("keyword", "source", "scope", "sort"))
        if key in seen:
            continue
        seen.add(key)
        normalized.append(item)
        if len(normalized) >= safe_limit:
            break
    return normalized


def _decode_search_history(value: object) -> list[dict[str, str]]:
    """Decode persisted JSON while tolerating old or manually edited values."""

    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        return []
    decoded: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for candidate in value:
        item = _normalize_search_history_entry(candidate)
        if item is None:
            continue
        key = tuple(item[field].casefold() for field in ("keyword", "source", "scope", "sort"))
        if key in seen:
            continue
        seen.add(key)
        decoded.append(item)
    return decoded


def _format_duration(seconds: float) -> str:
    total = max(0, int(seconds or 0))
    minutes, remainder = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{remainder:02d}"
    return f"{minutes}:{remainder:02d}"


def _format_count(value: int) -> str:
    value = int(value or 0)
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return str(value)


def _short_text(value: str, length: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= length else f"{text[: max(1, length - 1)]}…"


def _grid_item_height(
    list_widget: ListWidget, width: int, icon_size: QSize, fallback_lines: int
) -> int:
    """Measure complete cards with the font and padding used by their delegate."""

    delegate = list_widget.itemDelegate()
    style = list_widget.style()
    base_option = QStyleOptionViewItem()
    list_widget.initViewItemOption(base_option)
    base_option.rect = QRect(0, 0, max(1, int(width)), 10000)
    height = (
        icon_size.height()
        + QFontMetrics(base_option.font).lineSpacing() * max(1, fallback_lines)
    )
    for index in range(list_widget.count()):
        item = list_widget.item(index)
        if item is None:
            continue
        option = QStyleOptionViewItem(base_option)
        delegate.initStyleOption(option, list_widget.indexFromItem(item))
        option.decorationSize = icon_size
        # The selected border is thicker. Reserve its space for every card so
        # selecting a result cannot hide the final caption line.
        option.state |= QStyle.StateFlag.State_Selected
        text_rect = style.subElementRect(
            QStyle.SubElement.SE_ItemViewItemText, option, list_widget
        )
        cover_rect = style.subElementRect(
            QStyle.SubElement.SE_ItemViewItemDecoration, option, list_widget
        )
        # Measure the painting width rather than sizeFromContents(), whose
        # wider natural size can omit a wrapped line in narrow columns. The
        # card style uses the same border/padding above and below its content.
        bottom_inset = max(0, cover_rect.top() - option.rect.top())
        height = max(height, text_rect.bottom() - option.rect.top() + 1 + bottom_inset)
    # Fluent's delegate shrinks the painting rectangle by its vertical margin.
    return height + 2 * max(0, int(getattr(delegate, "margin", 0)))


def _extract_iwara_video_id(value: str) -> str:
    match = re.search(r"/video/([^/?#]+)", str(value or ""))
    return match.group(1) if match else ""


def _oreno3d_video_url(video: SearchVideo) -> str:
    """Return the original Oreno3D URL even after bridge hydration mutates the card."""

    raw = video.raw if isinstance(video.raw, dict) else {}
    return str(
        raw.get("oreno3d_url")
        or (video.source_url if video.source_kind == "oreno3d" else "")
        or ""
    ).strip()


def _author_subscription_target(
    value: SearchAuthor | SearchVideo | object,
) -> tuple[str, str, str, str] | None:
    """Return ``(username, title, remote_id, avatar_url)`` for one result."""

    def _field_text(candidate: object, *keys: str) -> str:
        if isinstance(candidate, dict):
            for key in keys:
                raw_value = candidate.get(key)
                if isinstance(raw_value, str) and raw_value.strip():
                    return raw_value.strip()
        return ""

    def _author_key(candidate: object) -> str:
        text = str(candidate or "").strip()
        if not text:
            return ""
        # Search adapters may return a profile URL instead of a username.
        # Keep the last profile segment so the subscription store receives the
        # same key as a manually entered author subscription.
        match = re.search(r"/(?:profile|user|author)/([^/?#]+)", text, re.IGNORECASE)
        if match:
            text = match.group(1)
        return text.lstrip("@").strip().strip("/")

    if isinstance(value, SearchAuthor):
        raw = value.raw if isinstance(value.raw, dict) else {}
        raw_profile = raw.get("profile") if isinstance(raw.get("profile"), dict) else {}
        username = _author_key(
            value.username
            or _field_text(raw, "username", "slug", "author_username")
            or _field_text(raw_profile, "username", "slug")
        )
        if not username:
            return None
        return (
            username,
            str(value.name or _field_text(raw, "name", "displayName") or username).strip() or username,
            str(value.author_id or _field_text(raw, "id", "userId", "author_id")).strip(),
            str(value.avatar_url or _field_text(raw, "avatar_url", "avatar", "image")).strip(),
        )
    if not isinstance(value, SearchVideo):
        return None

    raw = value.raw if isinstance(value.raw, dict) else {}
    # Oreno author resolution may use a surviving work on the author page
    # rather than the selected (possibly deleted) Iwara video.  Keep that
    # mapping independent from the bridge video's hydration flag.
    mapped_author = raw.get("oreno_iwara_author")
    if isinstance(mapped_author, dict):
        username = _author_key(
            mapped_author.get("username")
            or mapped_author.get("slug")
            or mapped_author.get("profile_url")
        )
        if username:
            return (
                username,
                str(mapped_author.get("name") or username).strip() or username,
                str(mapped_author.get("id") or "").strip(),
                str(mapped_author.get("avatar_url") or "").strip(),
            )
    # An Oreno3D card carries Oreno's uploader name, which is not an Iwara
    # account.  Do not turn that bridge-side label into a local Iwara source
    # until the linked Iwara video metadata has been hydrated.
    if raw.get("oreno3d_url") and raw.get("_iwara_metadata_loaded") is not True:
        return None
    raw_user = raw.get("user") if isinstance(raw.get("user"), dict) else {}
    raw_author = raw.get("author") if isinstance(raw.get("author"), dict) else {}
    bridge_metadata_loaded = bool(
        raw.get("oreno3d_url") and raw.get("_iwara_metadata_loaded") is True
    )
    if bridge_metadata_loaded:
        # Once the bridge has been hydrated, only fields from the Iwara
        # payload may identify the account.  Falling back to the Oreno card's
        # uploader here would silently subscribe the wrong site user.
        username = _author_key(
            _field_text(raw_user, "username", "slug", "handle")
            or _field_text(raw_author, "username", "slug", "handle")
            or _field_text(raw, "username", "slug", "author_username")
        )
    else:
        username = _author_key(
            value.author_username
            or _field_text(raw_user, "username", "slug", "handle")
            or _field_text(raw_author, "username", "slug", "handle")
            or _field_text(raw, "username", "slug", "author_username", "author_url")
            or value.author_name
            or _field_text(raw, "author_name", "author")
        )
    if not username:
        return None
    if bridge_metadata_loaded:
        title = str(
            _field_text(raw_user, "name", "displayName")
            or _field_text(raw_author, "name", "displayName")
            or _field_text(raw, "author_name")
            or username
        ).strip() or username
    else:
        title = str(
            value.author_name
            or _field_text(raw_user, "name", "displayName")
            or _field_text(raw_author, "name", "displayName")
            or _field_text(raw, "author_name", "author")
            or username
        ).strip() or username
    remote_id = str(
        _field_text(raw_user, "id", "userId", "author_id")
        or _field_text(raw_author, "id", "userId", "author_id")
        or _field_text(raw, "user_id", "author_id")
    ).strip()
    avatar_url = str(
        _field_text(raw_user, "avatar_url", "avatar", "image")
        or _field_text(raw_author, "avatar_url", "avatar", "image")
        or _field_text(raw, "avatar_url", "author_avatar")
    ).strip()
    return username, title, remote_id, avatar_url


def _author_source_info(value: SearchAuthor | SearchVideo | object) -> tuple[str, str]:
    """Return a durable author page URL and its source origin for one result."""

    if isinstance(value, SearchAuthor):
        return str(value.source_url or "").strip(), "iwara"
    if isinstance(value, SearchVideo):
        raw = value.raw if isinstance(value.raw, dict) else {}
        if value.source_kind == "oreno3d" or raw.get("oreno3d_url"):
            return str(raw.get("oreno3d_author_url") or "").strip(), "oreno3d"
    return "", ""


def _search_grid_style() -> str:
    p = palette()
    # Scope the shared scrollbar rules to this list's own bars.
    scrollbars = scrollbar_qss().replace("QScrollBar", "QListWidget QScrollBar")
    return f"""
        QListWidget {{
            background: transparent;
            border: none;
            outline: none;
        }}
        QListWidget::item {{
            background: {p.surface};
            border: 1px solid {p.border};
            border-radius: 8px;
            padding: 6px;
            color: {p.text};
        }}
        QListWidget::item:hover {{
            background: {p.surface};
            border: 1px solid {p.accent};
        }}
        QListWidget::item:selected,
        QListWidget::item:selected:active,
        QListWidget::item:selected:!active {{
            background: {p.selected};
            border: 2px solid {p.accent};
            color: {p.selected_text};
        }}
        {scrollbars}
    """


class TagSuggestionPopup(ListWidget):
    """Small Fluent-compatible popup for localized tag candidates."""

    suggestion_chosen = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        # ``Qt.Popup`` grabs the application's keyboard even with
        # ``NoFocus``.  That makes the first typed character open the list and
        # prevents the user from continuing to type a tag.  A non-activating
        # tool window keeps mouse selection while leaving the LineEdit active.
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setMinimumWidth(360)
        self.setMaximumHeight(260)
        self.setStyleSheet(popup_list_qss())
        self.itemClicked.connect(self._choose_item)

    def showEvent(self, event):
        # Popups outlive theme switches; recolor them whenever they open.
        self.setStyleSheet(popup_list_qss())
        super().showEvent(event)

    def set_suggestions(self, suggestions: list[TagSuggestion]):
        self.clear()
        for suggestion in suggestions:
            item = QListWidgetItem(suggestion.display_text)
            item.setData(Qt.ItemDataRole.UserRole, suggestion.key)
            item.setToolTip(
                f"{suggestion.key}\n"
                f"EN: {suggestion.en}\n"
                f"中文: {suggestion.zh}\n"
                f"日本語: {suggestion.ja}"
            )
            self.addItem(item)
        if self.count():
            self.setCurrentRow(0)

    def _choose_item(self, item: QListWidgetItem):
        value = item.data(Qt.ItemDataRole.UserRole)
        if value:
            self.suggestion_chosen.emit(str(value))


class SearchKeywordEdit(LineEdit):
    """Line edit that lets the page anchor an on-demand history popup."""

    activated = Signal()
    deactivated = Signal()

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.activated.emit()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.deactivated.emit()

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        self.activated.emit()


class SearchHistoryPopup(ListWidget):
    """Non-activating dropdown anchored to the search keyword field."""

    history_chosen = Signal(str)
    clear_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setMinimumWidth(360)
        self.setMaximumHeight(280)
        self.setStyleSheet(popup_list_qss())
        self.itemClicked.connect(self._choose_item)

    def showEvent(self, event):
        self.setStyleSheet(popup_list_qss())
        super().showEvent(event)

    def set_history(self, entries: list[dict[str, str]], formatter):
        self.clear()
        for entry in entries:
            item = QListWidgetItem(formatter(entry))
            item.setData(
                Qt.ItemDataRole.UserRole,
                json.dumps(entry, ensure_ascii=False, separators=(",", ":")),
            )
            self.addItem(item)
        if entries:
            clear_item = QListWidgetItem(
                tr("Clear search history", "清空搜索历史", "検索履歴を消去")
            )
            clear_item.setData(Qt.ItemDataRole.UserRole, "__clear__")
            self.addItem(clear_item)
            self.setCurrentRow(0)

    def _choose_item(self, item: QListWidgetItem):
        value = str(item.data(Qt.ItemDataRole.UserRole) or "")
        self.hide()
        if value == "__clear__":
            self.clear_requested.emit()
        elif value:
            self.history_chosen.emit(value)


