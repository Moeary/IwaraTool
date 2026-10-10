"""Download confirmations the user put off: kept on disk until their videos are downloaded.

Cancelling a download confirmation does not throw the selection away: it
becomes a one-off "stack" listed on the download workbench, which re-opens the
same confirmation.  A stack disappears once every video it would download is
in the history, or when the user clears it.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from typing import Any

from ...config import app_config
from ...signal_bus import signal_bus
from .listing_preview import is_downloadable


class PendingStackStore:
    """A small JSON file of stacks: label, rule, and the videos with their checked state."""

    def __init__(self, path: str | None = None):
        self._path = path
        self._lock = threading.Lock()

    @property
    def path(self) -> str:
        return self._path or os.path.join(app_config.app_data_dir, "pending_downloads.json")

    def load(self) -> list[dict[str, Any]]:
        with self._lock:
            return self._read()

    def _read(self) -> list[dict[str, Any]]:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return []
        return [stack for stack in data if isinstance(stack, dict) and stack.get("id")] if isinstance(data, list) else []

    def _write(self, stacks: list[dict[str, Any]]):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        temp_path = f"{self.path}.tmp"
        with open(temp_path, "w", encoding="utf-8") as fh:
            json.dump(stacks, fh, ensure_ascii=False)
        os.replace(temp_path, self.path)

    def update(self, change) -> Any:
        """Run ``change(stacks)`` (it edits the list in place) and save; returns its result."""

        with self._lock:
            stacks = self._read()
            result = change(stacks)
            self._write(stacks)
        signal_bus.pending_stacks_changed.emit()
        return result


def _stack_items(preview: dict[str, Any], checked_ids: set[str]) -> list[dict[str, Any]]:
    return [
        {
            "video_id": str(item["video_id"]),
            "raw": item.get("raw") or {"id": item["video_id"]},
            "checked": str(item["video_id"]) in checked_ids,
            # What this stack still owes: decided when the user last looked at it.
            "wanted": str(item["video_id"]) in checked_ids and is_downloadable(item),
        }
        for item in preview.get("items") or []
    ]


class PendingStacksMixin:
    """Download confirmations the user put off, kept until their videos are downloaded."""

    def save_pending_stack(self, preview: dict[str, Any], checked_ids: list[str], *, stack_id: str = "") -> str:
        """Create (or with ``stack_id`` update) a stack from a confirmation; returns its id."""

        items = _stack_items(preview, set(checked_ids))
        record = {
            "id": stack_id or uuid.uuid4().hex,
            "label": str(preview.get("label", "") or ""),
            "kind": str(preview.get("kind", "") or ""),
            "url": str(preview.get("url", "") or ""),
            "rule_id": str(preview.get("rule_id", "") or ""),
            "created_at": time.strftime("%Y-%m-%d %H:%M"),
            "items": items,
        }

        def change(stacks):
            for index, stack in enumerate(stacks):
                if stack["id"] == record["id"]:
                    record["created_at"] = stack.get("created_at", record["created_at"])
                    stacks[index] = record
                    return record["id"]
            stacks.append(record)
            return record["id"]

        return self.pending_stacks.update(change)

    def list_pending_stacks(self) -> list[dict[str, Any]]:
        """Summaries, newest first: id, label, created_at, total and still-wanted counts."""

        summaries = []
        for stack in reversed(self.pending_stacks.load()):
            items = stack.get("items") or []
            summaries.append({
                "id": stack["id"],
                "label": str(stack.get("label", "") or ""),
                "kind": str(stack.get("kind", "") or ""),
                "created_at": str(stack.get("created_at", "") or ""),
                "total": len(items),
                "wanted": sum(1 for item in items if item.get("wanted")),
            })
        return summaries

    def open_pending_stack(self, stack_id: str) -> dict[str, Any] | None:
        """The stack as a fresh preview (current files, queue and rule), with its checked videos."""

        stack = next((s for s in self.pending_stacks.load() if s["id"] == stack_id), None)
        if stack is None:
            return None
        base = {"url": stack.get("url", ""), "kind": stack.get("kind", ""), "label": stack.get("label", ""),
                "items": [{"raw": item["raw"]} for item in stack.get("items") or []]}
        preview = self.reclassify_preview(base, str(stack.get("rule_id", "") or ""))
        checked = {item["video_id"] for item in stack.get("items") or [] if item.get("checked")}
        for item in preview["items"]:
            item["checked"] = item["video_id"] in checked
        preview["stack_id"] = stack_id
        return preview

    def remove_pending_stack(self, stack_id: str) -> bool:
        def change(stacks):
            before = len(stacks)
            stacks[:] = [s for s in stacks if s["id"] != stack_id]
            return len(stacks) != before

        return self.pending_stacks.update(change)

    def clear_pending_stacks(self) -> int:
        def change(stacks):
            count = len(stacks)
            stacks.clear()
            return count

        return self.pending_stacks.update(change)

    def prune_pending_stacks(self) -> int:
        """Drop stacks whose wanted videos are all in the history; returns how many went."""

        stacks = self.pending_stacks.load()
        if not stacks:
            return 0
        wanted = {item["video_id"] for s in stacks for item in s.get("items") or [] if item.get("wanted")}
        downloaded = {video_id.lower() for video_id in self.history.get_records(list(wanted))}
        done = {
            s["id"] for s in stacks
            if all(item["video_id"].lower() in downloaded for item in s.get("items") or [] if item.get("wanted"))
        }
        if not done:
            return 0

        def change(current):
            current[:] = [s for s in current if s["id"] not in done]
            return len(done)

        return self.pending_stacks.update(change)
