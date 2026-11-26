"""Subscription helpers: the Qt colours and URL opening; the rest is ``presentation.subscription.helpers``."""
from __future__ import annotations

import webbrowser
from typing import Any

from PySide6.QtGui import QColor

from ...presentation.subscription.helpers import (  # noqa: F401 - re-exported for the page modules
    _split_title_keywords,
    _title_matcher,
    _title_matches_keywords,
    _ellipsize,
    _source_type_label,
    _source_origin_label,
    _source_origin_role,
    _parse_ui_bool,
    _source_sort_label,
    _source_sort_key,
    _source_search_text,
    _item_state_text,
    _state_role,
    _item_not_downloadable,
    _task_status_from_value,
    _item_date_key,
    _extract_author_key,
    _extract_playlist_key,
    _detect_source_input,
    _source_url,
    _video_url,
    _parse_iwara_path,
    _date_only,
)
from ..style.theme import palette, qcolor


def _source_origin_color(source: dict[str, Any]) -> str:
    return getattr(palette(), _source_origin_role(source))


def _state_color(**state) -> QColor:
    return qcolor(_state_role(**state))


def _open_url(url: str):
    if url:
        webbrowser.open(url)
