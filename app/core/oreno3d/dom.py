"""A tiny HTML tree builder and the node queries the Oreno3D page parsers use."""

from __future__ import annotations

from html import unescape
from html.parser import HTMLParser
from typing import Any, Iterable


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


def _parse_tree(html: str) -> _Node:
    parser = _DomParser()
    parser.feed(html or "")
    parser.close()
    return parser.root
