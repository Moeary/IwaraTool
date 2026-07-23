"""Named download/filter rules persisted as a small portable JSON file."""
from __future__ import annotations

import json
import os
import uuid
from copy import deepcopy
from datetime import datetime
from typing import Any

from ..config import app_config


BUILTIN_DEFAULT_RULE_ID = "__builtin_default__"
ACTIVE_RULE_UI_KEY = "active_download_rule_id"

RULE_FILTER_KEYS = (
    "filter_enabled",
    "filter_min_likes_enabled",
    "filter_min_likes",
    "filter_min_views_enabled",
    "filter_min_views",
    "filter_date_enabled",
    "filter_start_date",
    "filter_end_date",
    "filter_include_tags_enabled",
    "filter_include_tags",
    "filter_exclude_tags_enabled",
    "filter_exclude_tags",
)
RULE_TITLE_KEYS = ("title_include", "title_exclude")
RULE_DOWNLOAD_KEYS = (
    "download_video_file",
    "download_thumbnail",
    "collect_nfo_info",
    "mark_submitted_as_downloaded",
)
# Only the filename template belongs to a reusable rule. Download location,
# de-duplication, and completed-task behavior remain global Settings.
RULE_STORAGE_KEYS = ("filename_template",)
RULE_KEYS = RULE_FILTER_KEYS + RULE_TITLE_KEYS + RULE_DOWNLOAD_KEYS + RULE_STORAGE_KEYS


def default_rule_payload() -> dict[str, Any]:
    """Return the built-in safe rule: no filters and video download only."""
    return {
        "filter_enabled": False,
        "filter_min_likes_enabled": False,
        "filter_min_likes": 0,
        "filter_min_views_enabled": False,
        "filter_min_views": 0,
        "filter_date_enabled": False,
        "filter_start_date": "1970-01-01",
        "filter_end_date": "",
        "filter_include_tags_enabled": False,
        "filter_include_tags": "",
        "filter_exclude_tags_enabled": False,
        "filter_exclude_tags": "",
        "title_include": "",
        "title_exclude": "",
        "download_video_file": True,
        "download_thumbnail": False,
        "collect_nfo_info": False,
        "mark_submitted_as_downloaded": False,
        # Naming is rule-specific; download location and task behavior are global.
        "filename_template": app_config.filename_template,
    }


def builtin_default_rule() -> dict[str, Any]:
    return {
        "id": BUILTIN_DEFAULT_RULE_ID,
        "name": "默认下载",
        "created_at": "",
        "updated_at": "",
        "builtin": True,
        "payload": default_rule_payload(),
    }


def current_rule_payload() -> dict[str, Any]:
    """Capture the current filter/download/naming settings as a rule."""
    payload = default_rule_payload()
    for key in RULE_FILTER_KEYS + RULE_DOWNLOAD_KEYS + RULE_STORAGE_KEYS:
        payload[key] = getattr(app_config, key)
    return payload


def normalize_rule_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Merge persisted data with defaults and normalize primitive values."""
    result = default_rule_payload()
    if isinstance(payload, dict):
        for key in RULE_KEYS:
            if key in payload:
                result[key] = payload[key]
    for key in (
        "filter_enabled",
        "filter_min_likes_enabled",
        "filter_min_views_enabled",
        "filter_date_enabled",
        "filter_include_tags_enabled",
        "filter_exclude_tags_enabled",
        "download_video_file",
        "download_thumbnail",
        "collect_nfo_info",
        "mark_submitted_as_downloaded",
    ):
        result[key] = bool(result[key])
    for key in ("filter_min_likes", "filter_min_views"):
        try:
            result[key] = max(0, int(result[key]))
        except (TypeError, ValueError):
            result[key] = 0
    for key in RULE_TITLE_KEYS + (
        "filter_start_date",
        "filter_end_date",
        "filter_include_tags",
        "filter_exclude_tags",
        "filename_template",
    ):
        result[key] = str(result[key] or "").strip()
    if not result["filter_start_date"]:
        result["filter_start_date"] = "1970-01-01"
    if not result["filename_template"]:
        result["filename_template"] = "{username}/{YYYY-MM-DD}_{title}_{id}.mp4"
    if result["download_video_file"] and result["mark_submitted_as_downloaded"]:
        result["mark_submitted_as_downloaded"] = False
    if not result["download_video_file"] and not result["mark_submitted_as_downloaded"]:
        # A rule must have an explicit primary action. Default to a real download.
        result["download_video_file"] = True
    return result


def apply_rule_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Apply a rule to the current global settings and return normalized data."""
    normalized = normalize_rule_payload(payload)
    for key in RULE_FILTER_KEYS + RULE_DOWNLOAD_KEYS + RULE_STORAGE_KEYS:
        setattr(app_config, key, normalized[key])
    return normalized


def active_rule_id() -> str:
    return str(app_config.get_ui_value(ACTIVE_RULE_UI_KEY, BUILTIN_DEFAULT_RULE_ID) or BUILTIN_DEFAULT_RULE_ID)


def set_active_rule_id(rule_id: str):
    app_config.set_ui_value(ACTIVE_RULE_UI_KEY, rule_id or BUILTIN_DEFAULT_RULE_ID)


class RuleStore:
    """Small JSON-backed store; intentionally independent from history.db."""

    def __init__(self, path: str | None = None):
        self.path = path or os.path.join(app_config.app_data_dir, "rules.json")

    def list_rules(self) -> list[dict[str, Any]]:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, ValueError, TypeError):
            raw = []
        if not isinstance(raw, list):
            raw = []
        result: list[dict[str, Any]] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            result.append({
                "id": str(entry.get("id") or uuid.uuid4().hex),
                "name": str(entry.get("name") or "未命名规则").strip() or "未命名规则",
                "created_at": str(entry.get("created_at") or ""),
                "updated_at": str(entry.get("updated_at") or ""),
                "builtin": False,
                "payload": normalize_rule_payload(entry.get("payload")),
            })
        return result

    def list_available(self) -> list[dict[str, Any]]:
        return [builtin_default_rule(), *self.list_rules()]

    def find(self, rule_id: str) -> dict[str, Any] | None:
        return next((rule for rule in self.list_available() if rule["id"] == rule_id), None)

    def save(self, name: str, payload: dict[str, Any], rule_id: str | None = None) -> dict[str, Any]:
        name = str(name or "").strip()
        if not name:
            raise ValueError("规则名称不能为空")
        if rule_id == BUILTIN_DEFAULT_RULE_ID:
            raise ValueError("内置默认规则不能直接修改，请先复制")
        now = datetime.now().isoformat(timespec="seconds")
        rules = self.list_rules()
        existing = next((rule for rule in rules if rule["id"] == rule_id), None) if rule_id else None
        if existing is None:
            rule = {
                "id": uuid.uuid4().hex,
                "name": name,
                "created_at": now,
                "updated_at": now,
                "builtin": False,
                "payload": normalize_rule_payload(payload),
            }
            rules.append(rule)
        else:
            existing["name"] = name
            existing["updated_at"] = now
            existing["payload"] = normalize_rule_payload(payload)
            rule = existing
        self._write(rules)
        return deepcopy(rule)

    def delete(self, rule_id: str) -> bool:
        if rule_id == BUILTIN_DEFAULT_RULE_ID:
            return False
        rules = self.list_rules()
        kept = [rule for rule in rules if rule["id"] != rule_id]
        if len(kept) == len(rules):
            return False
        self._write(kept)
        return True

    def _write(self, rules: list[dict[str, Any]]):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        temp_path = self.path + ".tmp"
        serializable = [{k: v for k, v in rule.items() if k != "builtin"} for rule in rules]
        with open(temp_path, "w", encoding="utf-8") as fh:
            json.dump(serializable, fh, ensure_ascii=False, indent=2)
        os.replace(temp_path, self.path)


rule_store = RuleStore()
