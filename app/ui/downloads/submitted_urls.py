"""Turn pasted text into the list of URLs to submit."""

from __future__ import annotations

import re


_URL_SPLIT_RE = re.compile(r"[\s,，；;]+")


def split_submitted_urls(text: str) -> list[str]:
    """Split pasted text into unique http(s) URLs, keeping their order.

    A single non-URL token is returned unchanged so the resolver can still
    report a precise error for it.
    """
    tokens = [token.strip() for token in _URL_SPLIT_RE.split(str(text or "")) if token.strip()]
    urls: list[str] = []
    for token in tokens:
        if token.lower().startswith(("http://", "https://")) and token not in urls:
            urls.append(token)
    if urls:
        return urls
    stripped = str(text or "").strip()
    return [stripped] if stripped else []
