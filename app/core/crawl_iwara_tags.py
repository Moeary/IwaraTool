"""Crawl Iwara tags from /tags endpoint and export JSON + Markdown.

Usage:
    pixi run python app/core/crawl_iwara_tags.py
"""
from __future__ import annotations

import argparse
import json
import string
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cloudscraper

DEFAULT_BASE_URL = "https://apiq.iwara.tv/tags"
DEFAULT_FILTERS = string.ascii_uppercase + string.digits


def _text(v: Any) -> str:
    return str(v or "").strip()


def _load_translation_map(path: str) -> dict[str, dict[str, str]]:
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, str]] = {}
    for key, value in raw.items():
        if not isinstance(value, dict):
            continue
        out[str(key).strip().lower()] = {
            "en": _text(value.get("en")),
            "zh": _text(value.get("zh")),
            "ja": _text(value.get("ja")),
        }
    return out


def _payload_results(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("results", "items", "data", "tags"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
        if any(k in payload for k in ("id", "slug", "name", "title", "tag")):
            return [payload]
    return []


def _tag_item(raw: Any) -> dict[str, str] | None:
    if isinstance(raw, str):
        name = _text(raw)
        if not name:
            return None
        return {
            "key": name.lower(),
            "id": "",
            "slug": name,
            "name": name,
        }
    if not isinstance(raw, dict):
        return None

    tag_id = _text(raw.get("id"))
    slug = _text(raw.get("slug") or raw.get("tag"))
    name = (
        _text(raw.get("name"))
        or _text(raw.get("title"))
        or slug
        or tag_id
    )
    key = (slug or name or tag_id).lower()
    if not key:
        return None
    return {
        "key": key,
        "id": tag_id,
        "slug": slug or name,
        "name": name or key,
    }


def _fetch_page(
    scraper: cloudscraper.CloudScraper,
    base_url: str,
    filter_char: str,
    page: int,
) -> list[Any]:
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://www.iwara.tv",
        "Referer": "https://www.iwara.tv/",
        "X-Site": "www.iwara.tv",
    }
    resp = scraper.get(
        base_url,
        params={"filter": filter_char, "page": page},
        headers=headers,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    return _payload_results(data)


def _write_markdown(path: Path, tags: list[dict[str, Any]], filters: str):
    summary: dict[str, int] = {}
    for row in tags:
        key = _text(row.get("key")).strip()
        if not key:
            continue
        head = key[0].upper()
        if not head.isalnum():
            head = "#"
        summary[head] = summary.get(head, 0) + 1

    lines: list[str] = []
    lines.append("# Iwara Tags Index")
    lines.append("")
    lines.append(f"- Generated at: {datetime.now(timezone.utc).isoformat()}")
    lines.append(f"- Filters crawled: `{filters}`")
    lines.append(f"- Total tags: **{len(tags)}**")
    lines.append("")
    lines.append("## Summary (by first character)")
    lines.append("")
    lines.append("| filter | count |")
    lines.append("|---|---:|")
    for ch in filters:
        lines.append(f"| `{ch}` | {summary.get(ch, 0)} |")
    if "#" in summary:
        lines.append(f"| `#` | {summary.get('#', 0)} |")
    lines.append("")
    lines.append("## Table")
    lines.append("")
    lines.append("| # | key | en | zh | ja |")
    lines.append("|---:|---|---|---|---|")
    for idx, row in enumerate(tags, start=1):
        key = row.get("key", "")
        en = row.get("name_en", "")
        zh = row.get("name_zh", "")
        ja = row.get("name_ja", "")
        lines.append(f"| {idx} | `{key}` | {en} | {zh} | {ja} |")
    lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Crawl Iwara tags from /tags endpoint.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Tags endpoint URL")
    parser.add_argument("--filters", default=DEFAULT_FILTERS, help="Filter characters (A-Z0-9)")
    parser.add_argument("--max-pages-per-filter", type=int, default=80, help="Max pages for each filter")
    parser.add_argument("--sleep", type=float, default=0.15, help="Sleep seconds between requests")
    parser.add_argument("--translation-map", default="", help="Optional JSON map: {key: {en, zh, ja}}")
    parser.add_argument("--output-json", default="data/iwara_tags.json", help="Output JSON path")
    parser.add_argument("--output-md", default="docs/iwara_tags.md", help="Output Markdown path")
    args = parser.parse_args()

    filters = "".join([c for c in args.filters if c.isalnum()]).upper()
    if not filters:
        filters = DEFAULT_FILTERS

    translation_map = _load_translation_map(args.translation_map)
    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False}
    )

    tags_by_key: dict[str, dict[str, Any]] = {}
    for fch in filters:
        print(f"[filter] {fch}")
        for page in range(max(1, args.max_pages_per_filter)):
            try:
                results = _fetch_page(scraper, args.base_url, fch, page)
            except Exception as exc:
                print(f"[warn] filter={fch} page={page} failed: {exc}")
                break

            if not results:
                print(f"[done] filter={fch} page={page} no results")
                break

            for raw in results:
                item = _tag_item(raw)
                if not item:
                    continue
                key = item["key"]
                row = tags_by_key.setdefault(
                    key,
                    {
                        "key": key,
                        "id": item["id"],
                        "slug": item["slug"],
                        "name": item["name"],
                        "seen_filters": set(),
                    },
                )
                if not row.get("id"):
                    row["id"] = item["id"]
                if not row.get("slug"):
                    row["slug"] = item["slug"]
                if not row.get("name"):
                    row["name"] = item["name"]
                row["seen_filters"].add(fch)

            print(f"[page] filter={fch} page={page} total_tags={len(tags_by_key)}")
            time.sleep(max(0.0, args.sleep))

    tags_out: list[dict[str, Any]] = []
    for key in sorted(tags_by_key):
        row = tags_by_key[key]
        name = _text(row.get("name")) or key
        tm = translation_map.get(key, {})
        tags_out.append(
            {
                "key": key,
                "id": _text(row.get("id")),
                "slug": _text(row.get("slug")),
                "name": name,
                "name_en": _text(tm.get("en")) or name,
                "name_zh": _text(tm.get("zh")) or name,
                "name_ja": _text(tm.get("ja")) or name,
                "seen_filters": sorted(list(row.get("seen_filters", set()))),
            }
        )

    output_json = Path(args.output_json)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "source": args.base_url,
                "filters": filters,
                "count": len(tags_out),
                "tags": tags_out,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"[ok] json: {output_json}")

    output_md = Path(args.output_md)
    _write_markdown(output_md, tags_out, filters)
    print(f"[ok] markdown: {output_md}")


if __name__ == "__main__":
    main()
