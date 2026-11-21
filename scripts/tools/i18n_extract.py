"""Collect every ``tr(en, zh, ja)`` call in ``app/`` into one catalogue.

The app keeps its three languages inline at each call site (see app/i18n.py).
This script reads them with ``ast`` without importing the app, so translators
and reviewers can see all the texts in one place:

    pixi run python scripts/tools/i18n_extract.py                       # summary
    pixi run python scripts/tools/i18n_extract.py --conflicts           # one English text, several translations
    pixi run python scripts/tools/i18n_extract.py --output strings.json  # full catalogue as JSON
    pixi run python scripts/tools/i18n_extract.py --format csv --output strings.csv

Each entry keeps the English text (an f-string becomes its source, such as
``{days} d ago``), the Chinese and Japanese texts and every place it is used.
The same English text translated more than one way is a conflict. Some are on
purpose (Cancel is 取消 in a dialog but 中断 for a task); review the rest.
"""

from __future__ import annotations

import argparse
import ast
import csv
import io
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Entry:
    en: str
    zh: str
    ja: str
    dynamic: bool
    places: list[str] = field(default_factory=list)


def _text(node: ast.expr) -> tuple[str, bool]:
    """A string argument as text; anything else as its source, marked dynamic."""

    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value, False
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            if isinstance(value, ast.Constant):
                parts.append(str(value.value).replace("{", "{{").replace("}", "}}"))
            elif isinstance(value, ast.FormattedValue):
                parts.append("{" + ast.unparse(value.value) + "}")
        return "".join(parts), True
    return ast.unparse(node), True


def collect(source_dir: Path) -> list[Entry]:
    entries: dict[tuple[str, str, str], Entry] = {}
    for path in sorted(source_dir.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        rel = path.relative_to(ROOT).as_posix()
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "tr"):
                continue
            args: list[ast.expr | None] = list(node.args[:3]) + [None] * (3 - len(node.args[:3]))
            for keyword in node.keywords:
                if keyword.arg in ("en_text", "zh_text", "ja_text"):
                    args[("en_text", "zh_text", "ja_text").index(keyword.arg)] = keyword.value
            if args[0] is None or args[1] is None:
                continue
            texts = [_text(arg) for arg in args if arg is not None]
            while len(texts) < 3:
                texts.append(texts[0])  # tr() falls back to English
            key = (texts[0][0], texts[1][0], texts[2][0])
            entry = entries.get(key)
            if entry is None:
                entry = entries[key] = Entry(*key, dynamic=any(dyn for _, dyn in texts))
            entry.places.append(f"{rel}:{node.lineno}")
    return sorted(entries.values(), key=lambda e: (e.en.lower(), e.zh, e.ja))


def conflicts(entries: list[Entry]) -> dict[str, list[Entry]]:
    by_en: dict[str, list[Entry]] = defaultdict(list)
    for entry in entries:
        by_en[entry.en].append(entry)
    return {en: group for en, group in by_en.items() if len(group) > 1}


def render_conflicts(groups: dict[str, list[Entry]]) -> str:
    lines = []
    for en, group in sorted(groups.items(), key=lambda kv: kv[0].lower()):
        lines.append(f"{en!r}")
        for entry in group:
            lines.append(f"    zh={entry.zh!r}  ja={entry.ja!r}")
            for place in entry.places:
                lines.append(f"        {place}")
    lines.append(f"\n{len(groups)} English texts have more than one translation.")
    return "\n".join(lines)


def render_json(entries: list[Entry]) -> str:
    rows = [
        {"en": e.en, "zh": e.zh, "ja": e.ja, "dynamic": e.dynamic, "places": e.places}
        for e in entries
    ]
    return json.dumps(rows, ensure_ascii=False, indent=2) + "\n"


def render_csv(entries: list[Entry]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["en", "zh", "ja", "dynamic", "places"])
    for e in entries:
        writer.writerow([e.en, e.zh, e.ja, int(e.dynamic), " ".join(e.places)])
    return buffer.getvalue()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--source", type=Path, default=ROOT / "app", help="directory to scan (default: app/)")
    parser.add_argument("--conflicts", action="store_true", help="list English texts translated more than one way")
    parser.add_argument("--format", choices=("json", "csv"), default="json", help="catalogue format for --output")
    parser.add_argument("--output", type=Path, help="write the full catalogue to this file")
    args = parser.parse_args(argv)

    entries = collect(args.source.resolve())
    if args.conflicts:
        print(render_conflicts(conflicts(entries)))
    if args.output:
        text = render_json(entries) if args.format == "json" else render_csv(entries)
        args.output.write_text(text, encoding="utf-8", newline="")
        print(f"Wrote {len(entries)} entries to {args.output}", file=sys.stderr)
    if not args.conflicts and not args.output:
        calls = sum(len(e.places) for e in entries)
        english = len({e.en for e in entries})
        dynamic = sum(1 for e in entries if e.dynamic)
        print(
            f"{calls} tr() calls, {len(entries)} distinct translations, "
            f"{english} distinct English texts, {dynamic} with placeholders, "
            f"{len(conflicts(entries))} English texts with more than one translation"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
