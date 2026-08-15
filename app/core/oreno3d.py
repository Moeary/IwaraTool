"""Small Oreno3D HTML client for online search and bounded detail lookups.

The reference project uses ``selectolax``.  IwaraTool deliberately keeps its
current dependency set small, so this module contains a conservative DOM
parser for the stable Oreno3D selectors used by its listing/detail pages.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
from typing import Any, Iterable
from urllib.parse import parse_qs, quote, urljoin, urlparse

from .oreno3d_mapping import resolve_oreno3d_entity


BASE_URL = "https://oreno3d.com"
_ICON_PREFIXES = {"face", "local_library", "accessibility_new", "local_offer"}
_ENTITY_PATHS = {
    "tag": "tags",
    "tags": "tags",
    "origin": "origins",
    "origins": "origins",
    "character": "characters",
    "characters": "characters",
}


@dataclass(slots=True, frozen=True)
class Oreno3DEntity:
    source_id: str
    name: str
    url: str


@dataclass(slots=True)
class Oreno3DListing:
    source_id: str
    oreno3d_url: str
    title: str
    author_name: str
    thumbnail_url: str
    view_count: int | None
    favorite_count: int | None
    tags: tuple[str, ...] = ()


@dataclass(slots=True)
class Oreno3DDetail:
    source_id: str
    oreno3d_url: str
    title: str
    external_video_url: str
    author: Oreno3DEntity | None
    tags: list[Oreno3DEntity] = field(default_factory=list)
    origins: list[Oreno3DEntity] = field(default_factory=list)
    characters: list[Oreno3DEntity] = field(default_factory=list)
    thumbnail_url: str = ""
    published_at: str = ""
    view_count: int | None = None
    favorite_count: int | None = None
    author_comment: str = ""


class _Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag: str = "root", attrs: dict[str, str] | None = None):
        self.tag = tag
        self.attrs = attrs or {}
        self.children: list[_Node | str] = []
        self.parent: _Node | None = None

    def add(self, child: _Node | str):
        if isinstance(child, _Node):
            child.parent = self
        self.children.append(child)

    def text(self) -> str:
        parts: list[str] = []
        for child in self.children:
            if isinstance(child, _Node):
                if child.tag not in {"script", "style", "noscript"}:
                    parts.append(child.text())
            else:
                parts.append(child)
        return " ".join(" ".join(parts).split())


class _DomParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node()
        self._stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        node = _Node(tag.lower(), {key.lower(): value or "" for key, value in attrs})
        self._stack[-1].add(node)
        if tag.lower() not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            self._stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]):
        self.handle_starttag(tag, attrs)
        if self._stack[-1].tag == tag.lower():
            self._stack.pop()

    def handle_endtag(self, tag: str):
        tag = tag.lower()
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == tag:
                del self._stack[index:]
                return

    def handle_data(self, data: str):
        if data:
            self._stack[-1].add(data)


def _nodes(node: _Node, *, include_self: bool = False) -> Iterable[_Node]:
    if include_self:
        yield node
    for child in node.children:
        if isinstance(child, _Node):
            yield child
            yield from _nodes(child)


def _classes(node: _Node) -> set[str]:
    return {item for item in node.attrs.get("class", "").split() if item}


def _all(node: _Node, *, tag: str | None = None, class_name: str | None = None) -> list[_Node]:
    return [
        candidate
        for candidate in _nodes(node)
        if (tag is None or candidate.tag == tag)
        and (class_name is None or class_name in _classes(candidate))
    ]


def _first(node: _Node, *, tag: str | None = None, class_name: str | None = None) -> _Node | None:
    return next(iter(_all(node, tag=tag, class_name=class_name)), None)


def _direct_children(node: _Node, *, tag: str | None = None) -> list[_Node]:
    return [child for child in node.children if isinstance(child, _Node) and (tag is None or child.tag == tag)]


def _attr(node: _Node | None, key: str) -> str:
    return str(node.attrs.get(key.lower(), "") if node else "").strip()


def _clean_text(value: Any) -> str:
    text = unescape(str(value or ""))
    return " ".join(text.replace("\xa0", " ").split()).strip()


def _absolute(url: str) -> str:
    return urljoin(BASE_URL + "/", _clean_text(url)) if url else ""


def _parse_compact_number(value: str) -> int | None:
    text = _clean_text(value).replace(",", "")
    if not text:
        return None
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*([kKmMbB])?", text)
    if not match:
        return None
    try:
        number = float(match.group(1))
    except ValueError:
        return None
    multiplier = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}.get(
        (match.group(2) or "").casefold(), 1
    )
    return int(number * multiplier)


def _movie_id(url: str) -> str:
    match = re.search(r"/movies/([^/?#]+)", urlparse(url).path)
    return match.group(1) if match else ""


def _entity_id(url: str, kind: str) -> str:
    match = re.search(rf"/{re.escape(kind)}/([^/?#]+)", urlparse(url).path)
    return match.group(1) if match else ""


def _entity_name(anchor: _Node) -> str:
    for class_name in ("tag-text", "aside-list-name", "box-text-in", "video-center"):
        child = _first(anchor, class_name=class_name)
        value = _clean_text(child.text() if child else "")
        if value and value not in _ICON_PREFIXES:
            return value
    value = _clean_text(anchor.text())
    while True:
        head, separator, tail = value.partition(" ")
        if separator and head in _ICON_PREFIXES:
            value = tail.strip()
        else:
            return value


def _entity(anchor: _Node | None, kind: str) -> Oreno3DEntity | None:
    if anchor is None:
        return None
    url = _absolute(_attr(anchor, "href"))
    source_id = _entity_id(url, kind)
    name = _entity_name(anchor)
    if not url or not source_id or not name:
        return None
    return Oreno3DEntity(source_id=source_id, name=name, url=url)


def _parse_tree(html: str) -> _Node:
    parser = _DomParser()
    parser.feed(html or "")
    parser.close()
    return parser.root


def parse_listing_page(html: str, *, page: int) -> tuple[list[Oreno3DListing], int]:
    tree = _parse_tree(html)
    grids = _all(tree, tag="div", class_name="g-main-grid")
    articles: list[_Node] = []
    for grid in grids:
        articles.extend(_direct_children(grid, tag="article"))
    if not articles:
        articles = [article for article in _all(tree, tag="article") if _first(article, class_name="box")]

    items: list[Oreno3DListing] = []
    for article in articles:
        link = _first(article, tag="a", class_name="box")
        if link is None:
            link = next(
                (anchor for anchor in _all(article, tag="a") if "/movies/" in _absolute(_attr(anchor, "href"))),
                None,
            )
        url = _absolute(_attr(link, "href"))
        source_id = _movie_id(url)
        if not source_id:
            continue
        title_node = _first(article, tag="h2", class_name="box-h2")
        author_box = _first(article, class_name="box-text1")
        author_node = _first(author_box, class_name="box-text-in") if author_box else None
        image = _first(article, tag="img", class_name="main-thumbnail")
        stats = [_clean_text(node.text()) for node in _all(article, class_name="figure-text-in")]
        tag_box = _first(article, class_name="box-text2")
        tag_text = _clean_text(tag_box.text() if tag_box else "")
        tags = tuple(
            dict.fromkeys(
                part for part in re.split(r"\s+", tag_text) if part and part not in _ICON_PREFIXES
            )
        )
        items.append(
            Oreno3DListing(
                source_id=source_id,
                oreno3d_url=url,
                title=_clean_text(title_node.text() if title_node else "") or source_id,
                author_name=_clean_text(author_node.text() if author_node else ""),
                thumbnail_url=_absolute(_attr(image, "src") or _attr(image, "data-src")),
                view_count=_parse_compact_number(stats[0]) if stats else None,
                favorite_count=_parse_compact_number(stats[1]) if len(stats) > 1 else None,
                tags=tags,
            )
        )

    last_page = max(1, int(page))
    pagination = next((node for node in _all(tree, tag="ul") if "pagination" in _classes(node)), None)
    for link in _all(pagination, tag="a", class_name="page-link") if pagination else []:
        query = parse_qs(urlparse(_absolute(_attr(link, "href"))).query)
        try:
            last_page = max(last_page, int(query.get("page", [last_page])[0]))
        except (TypeError, ValueError):
            pass
    return items, last_page


def parse_detail_page(html: str, *, source_id: str, oreno3d_url: str) -> Oreno3DDetail:
    tree = _parse_tree(html)
    title_node = _first(tree, tag="h1", class_name="video-h1")
    title = _clean_text(title_node.text() if title_node else "")
    if not title:
        raise ValueError(f"Could not find Oreno3D title for {source_id}")

    meta_image = next(
        (
            node
            for node in _all(tree, tag="meta")
            if _attr(node, "property").casefold() == "og:image"
        ),
        None,
    )
    thumbnail_url = _absolute(_attr(meta_image, "content"))
    view_count = favorite_count = None
    date_texts: list[str] = []
    view_list = next((node for node in _all(tree, tag="ul") if "video-views" in _classes(node)), None)
    for label in _all(view_list, tag="li", class_name="f-label-in") if view_list else []:
        icon = _first(label, tag="i", class_name="material-icons")
        icon_name = _clean_text(icon.text() if icon else "")
        values = [_clean_text(node.text()) for node in _all(label, class_name="video-text")]
        if values and icon_name in {"event", "schedule", "access_time", "calendar_month", "date_range"}:
            date_texts.extend(values)
        elif values and any(re.match(r"\d{4}-\d{2}-\d{2}", value) for value in values):
            date_texts.extend(values)
        if icon_name == "remove_red_eye" and values:
            view_count = _parse_compact_number(values[0])
        elif icon_name == "favorite" and values:
            favorite_count = _parse_compact_number(values[0])
    published_at = ""
    date_texts = [value for value in date_texts if value]
    for index, value in enumerate(date_texts):
        if re.match(r"\d{4}-\d{2}-\d{2}", value):
            published_at = value
            if index + 1 < len(date_texts) and re.match(r"^\d{1,2}:\d{2}", date_texts[index + 1]):
                published_at = f"{value} {date_texts[index + 1]}"
            break

    sections = [node for node in _all(tree, tag="section") if "video-section-tag" in _classes(node)]
    entity_roots = sections or [tree]
    author = next(
        (
            _entity(anchor, "authors")
            for section in entity_roots
            for anchor in _all(section, tag="a")
            if "/authors/" in _attr(anchor, "href")
        ),
        None,
    )
    tags = _entities_many(entity_roots, "tags")
    origins = _entities_many(entity_roots, "origins")
    characters = _entities_many(entity_roots, "characters")
    external_video_url = ""
    for link in _all(tree, tag="a", class_name="video-watch-btn2"):
        href = _absolute(_attr(link, "href"))
        if "iwara" in href.casefold():
            external_video_url = href
            break
    comment_node = _first(tree, tag="blockquote", class_name="video-information-comment")
    return Oreno3DDetail(
        source_id=source_id,
        oreno3d_url=oreno3d_url,
        title=title,
        external_video_url=external_video_url,
        author=author,
        tags=tags,
        origins=origins,
        characters=characters,
        thumbnail_url=thumbnail_url,
        published_at=published_at,
        view_count=view_count,
        favorite_count=favorite_count,
        author_comment=_clean_text(comment_node.text() if comment_node else ""),
    )


def _entities(node: _Node, kind: str) -> list[Oreno3DEntity]:
    result: list[Oreno3DEntity] = []
    seen: set[str] = set()
    for anchor in _all(node, tag="a"):
        if f"/{kind}/" not in _attr(anchor, "href"):
            continue
        entity = _entity(anchor, kind)
        if entity and entity.source_id not in seen:
            result.append(entity)
            seen.add(entity.source_id)
    return result


def _entities_many(nodes: Iterable[_Node], kind: str) -> list[Oreno3DEntity]:
    result: list[Oreno3DEntity] = []
    seen: set[str] = set()
    for node in nodes:
        for entity in _entities(node, kind):
            if entity.source_id not in seen:
                result.append(entity)
                seen.add(entity.source_id)
    return result


class Oreno3DClient:
    """Synchronous client for user-triggered Oreno3D requests."""

    def __init__(self, session, *, timeout: int = 30, delay: float = 0.15):
        self.session = session
        self.timeout = max(5, int(timeout))
        self.delay = max(0.0, float(delay))

    def fetch_listing_page(self, page: int, *, sort: str = "latest") -> tuple[list[Oreno3DListing], int]:
        params = {"sort": sort, "page": max(1, int(page))}
        return self._fetch_listing_path("/", params=params, page=page)

    def _fetch_listing_path(
        self,
        path: str,
        *,
        params: dict[str, object],
        page: int,
    ) -> tuple[list[Oreno3DListing], int]:
        response = self.session.get(
            f"{BASE_URL}{path}",
            params=params,
            headers={"Accept": "text/html,application/xhtml+xml", "Referer": BASE_URL + "/"},
            timeout=self.timeout,
        )
        try:
            if int(getattr(response, "status_code", 0) or 0) >= 400:
                raise RuntimeError(f"Oreno3D HTTP {response.status_code}")
            return parse_listing_page(response.text, page=max(1, int(page)))
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()

    def fetch_search_page(
        self,
        keyword: str,
        page: int = 1,
        *,
        sort: str = "latest",
        search_type: str | None = None,
        entity_id: str | None = None,
    ) -> tuple[list[Oreno3DListing], int]:
        """Query Oreno3D's free-text or entity search endpoint.

        Oreno3D exposes the same card markup on ``/search`` and on numeric
        entity routes such as ``/tags/{id}``.  Human-readable labels are first
        resolved through the bundled strict-unique Iwara/Oreno map; unknown
        labels fall back to the site's regular keyword search instead of
        producing a 404.
        """

        normalized_type = _clean_text(search_type).casefold()
        normalized_id = _clean_text(entity_id).strip("/")
        if ":" in normalized_id:
            prefix, value = normalized_id.split(":", 1)
            if prefix.casefold() in _ENTITY_PATHS and value.strip():
                normalized_type = prefix.casefold()
                normalized_id = value.strip()
        if normalized_type in _ENTITY_PATHS and normalized_id and not normalized_id.isdigit():
            resolved = resolve_oreno3d_entity(normalized_id)
            if resolved is not None:
                normalized_type = resolved.kind
                normalized_id = resolved.entity_id
        path = "/search"
        params: dict[str, object] = {"page": max(1, int(page))}
        if (
            normalized_type in _ENTITY_PATHS
            and normalized_id
            and normalized_id.isdigit()
        ):
            path = f"/{_ENTITY_PATHS[normalized_type]}/{quote(normalized_id, safe='')}"
        else:
            # ``tag:azur_lane`` and similar inputs are names, not Oreno's
            # numeric entity IDs.  Its public search endpoint accepts these
            # names and is preferable to requesting /tags/azur_lane (404).
            params["keyword"] = _clean_text(keyword) or normalized_id
        if sort:
            params["sort"] = sort
        return self._fetch_listing_path(path, params=params, page=page)

    def fetch_entity_page(
        self,
        search_type: str,
        entity_id: str,
        page: int = 1,
        *,
        sort: str = "latest",
    ) -> tuple[list[Oreno3DListing], int]:
        """Fetch a tag, origin, or character result page."""

        return self.fetch_search_page(
            "",
            page=page,
            sort=sort,
            search_type=search_type,
            entity_id=entity_id,
        )

    def fetch_author_page(
        self,
        author_id_or_url: str,
        page: int = 1,
        *,
        sort: str = "latest",
    ) -> tuple[list[Oreno3DListing], int]:
        """Fetch one Oreno3D author's video listing.

        Author pages are stable even when an individual linked Iwara video is
        later removed, so callers can use this as the durable source for an
        author mapping and subscription entry.
        """

        value = _clean_text(author_id_or_url).strip()
        parsed = urlparse(value if "://" in value else "")
        path = parsed.path if parsed.path.startswith("/authors/") else ""
        if not path:
            author_id = value.strip("/").rsplit("/", 1)[-1]
            path = f"/authors/{quote(author_id, safe='')}"
        params: dict[str, object] = {"page": max(1, int(page))}
        if sort:
            params["sort"] = sort
        return self._fetch_listing_path(path, params=params, page=page)

    def fetch_detail_url(self, source_id: str, oreno3d_url: str) -> Oreno3DDetail:
        """Fetch one detail page without creating a local Oreno3D mirror."""

        response = self.session.get(
            oreno3d_url,
            headers={"Accept": "text/html,application/xhtml+xml", "Referer": BASE_URL + "/"},
            timeout=self.timeout,
        )
        try:
            if int(getattr(response, "status_code", 0) or 0) >= 400:
                raise RuntimeError(f"Oreno3D HTTP {response.status_code}")
            return parse_detail_page(
                response.text,
                source_id=str(source_id or "").strip(),
                oreno3d_url=oreno3d_url,
            )
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()

    def fetch_detail(self, item: Oreno3DListing) -> Oreno3DDetail:
        return self.fetch_detail_url(item.source_id, item.oreno3d_url)
