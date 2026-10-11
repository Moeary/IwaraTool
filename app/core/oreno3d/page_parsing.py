"""Parse Oreno3D listing and detail pages into listings, details and entities."""

from __future__ import annotations

import re
from typing import Iterable
from urllib.parse import parse_qs, urljoin, urlparse

from .dom import _Node, _all, _attr, _classes, _clean_text, _direct_children, _first, _parse_tree
from .models import Oreno3DDetail, Oreno3DEntity, Oreno3DListing


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


def extract_iwara_video_id(url: str) -> str:
    """Accept video links on Iwara itself, not mentions in unrelated URLs."""

    try:
        parsed = urlparse(_clean_text(url))
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
            "iwara.tv", "www.iwara.tv", "ecchi.iwara.tv",
        }:
            return ""
        match = re.match(r"^/video/([A-Za-z0-9_-]+)(?:/|$)", parsed.path)
        return match.group(1) if match else ""
    except ValueError:
        return ""


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
    # The sidebar and recommendations may contain other authors and links.
    article = _first(tree, tag="article", class_name="g-main-video-article") or tree
    title_node = _first(article, tag="h1", class_name="video-h1")
    if title_node is None:
        raise ValueError(f"Could not find Oreno3D title for {source_id}")
    # Older records can have an empty h1 while their source and author links
    # still exist. Reject non-detail pages, not these valid partial records.
    title = _clean_text(title_node.text()) or source_id

    meta_image = next(
        (
            node
            for node in _all(tree, tag="meta")
            if _attr(node, "property").casefold() == "og:image"
        ),
        None,
    )
    thumbnail_url = _absolute(_attr(meta_image, "content"))
    view_count, favorite_count, published_at = _detail_stats(tree)

    sections = [node for node in _all(article, tag="section") if "video-section-tag" in _classes(node)]
    entity_roots = sections or [article]
    authors = _entities_many(entity_roots, "authors")
    if not authors and article is not tree:
        authors = _entities(article, "authors")
    author = authors[0] if authors else None
    tags = _entities_many(entity_roots, "tags")
    origins = _entities_many(entity_roots, "origins")
    characters = _entities_many(entity_roots, "characters")
    external_video_url = _external_video_url(article)
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


def _detail_stats(tree) -> tuple[int | None, int | None, str]:
    """``(views, favorites, published_at)`` from the detail page's stats list."""

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
    return view_count, favorite_count, published_at


def _external_video_url(article) -> str:
    """The Iwara link of the poster / watch button; never one from the comments."""

    external_video_url = ""
    # Some records expose only the poster's play link. Both selectors are
    # used by the site's detail page; never scan comment URLs as a fallback.
    play_links = [
        anchor
        for figure in _all(article, class_name="video-figure")
        for anchor in _all(figure, tag="a")
    ]
    play_links.extend(_all(article, tag="a", class_name="video-watch-btn2"))
    for link in play_links:
        href = _absolute(_attr(link, "href"))
        if extract_iwara_video_id(href):
            external_video_url = href
            break
    return external_video_url


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
