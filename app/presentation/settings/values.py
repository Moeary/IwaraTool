"""Settings writes shared by both interfaces: clamping, parsing and side effects.

Each setter saves to ``app_config`` and announces the change the way the old
settings page always has (``signal_bus`` / ``manager.apply_config``), so
either interface can change a setting and the rest of the app follows.
Values come in as the widget gives them (text or numbers); the functions
return what was actually stored so the caller can show it.
"""
from __future__ import annotations

import json
from typing import Any

from ...config import DEFAULT_PROXY_URL, app_config
from ...core.download.policy import normalize_hhmm
from ...core.rules import BUILTIN_DEFAULT_RULE_ID
from ...i18n import tr
from .texts import stext


def _bus():
    from ...signal_bus import signal_bus

    return signal_bus


def clamp(value: Any, low: int, high: int, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(low, min(high, number))


def parse_int(text: Any, *, current: int, low: int, high: int, empty: int | None = None) -> int:
    """Typed number: blank gives ``empty`` (or ``current``), junk keeps ``current``."""
    raw = str(text if text is not None else "").strip()
    if not raw:
        return clamp(current if empty is None else empty, low, high, current)
    try:
        return clamp(int(raw), low, high, current)
    except ValueError:
        return clamp(current, low, high, current)


def ui_int(key: str, default: int, low: int, high: int) -> int:
    return clamp(app_config.get_ui_value(key, default) or default, low, high, default)


def ui_bool(key: str, default: bool) -> bool:
    value = app_config.get_ui_value(key, default)
    if isinstance(value, str):
        return value.strip().casefold() not in {"0", "false", "off", "no"}
    return bool(value)


# ── downloads ────────────────────────────────────────────────────────────────

CONCURRENCY_RANGE = (1, 10)
STALL_RANGE = (0, 3600)
RETRY_ATTEMPTS_RANGE = (1, 20)
SPEED_LIMIT_RANGE = (1, 10 * 1024 * 1024)
QUALITIES = ("Source", "540", "360")
COMPLETED_CLICK_ACTIONS = ("folder", "player")
SUBSCRIPTION_PROMPT_MODES = ("ask", "always", "never")


def set_max_concurrent(value: Any) -> int:
    value = parse_int(value, current=app_config.max_concurrent, low=CONCURRENCY_RANGE[0], high=CONCURRENCY_RANGE[1])
    app_config.max_concurrent = value
    return value


def set_stall_timeout(text: Any) -> int:
    value = parse_int(text, current=app_config.task_stall_timeout_seconds, low=STALL_RANGE[0], high=STALL_RANGE[1], empty=30)
    app_config.task_stall_timeout_seconds = value
    return value


def set_retry_keywords(retry: str, skip: str) -> None:
    app_config.auto_retry_failed_retry_keywords = str(retry or "").strip()
    app_config.auto_retry_failed_skip_keywords = str(skip or "").strip()


def set_quality(value: str) -> str:
    quality = value if value in QUALITIES else QUALITIES[0]
    app_config.preferred_quality = quality
    return quality


def temp_cleanup_report(removed: int, failed: int) -> tuple[bool, str, str, str]:
    """``(ok, title, message, log line)`` after removing ``*_temp`` files."""
    log = tr(
        f"[Cleanup] Temp cleanup done, removed {removed}, failed {failed}",
        f"[清理] 临时文件清理完成，删除 {removed} 个，失败 {failed} 个",
        f"[クリーンアップ] 一時ファイル削除完了: 削除 {removed} / 失敗 {failed}",
    )
    if failed:
        return False, stext("cleanup_finished_partial_failure"), tr(
            f"Removed {removed}, failed {failed}", f"已删除 {removed} 个，失败 {failed} 个", f"削除 {removed} 件、失敗 {failed} 件",
        ), log
    return True, stext("cleanup_finished"), tr(
        f"Removed {removed} *_temp files", f"已删除 {removed} 个 *_temp 文件", f"*_temp ファイルを {removed} 件削除しました",
    ), log


def background_changed(manager: Any) -> None:
    """Speed limit, schedule, refresh or notification settings changed: apply them now."""
    manager.resume_scheduled_downloads()
    _bus().background_settings_changed.emit()


def set_schedule(start: str, end: str) -> tuple[str, str]:
    start, end = normalize_hhmm(start), normalize_hhmm(end)
    app_config.download_schedule_start = start
    app_config.download_schedule_end = end
    return start, end


def auto_enqueue_rule_id() -> str:
    return str(app_config.subscription_auto_enqueue_rule_id or BUILTIN_DEFAULT_RULE_ID)


# ── network ──────────────────────────────────────────────────────────────────


def set_api_proxy_enabled(enabled: bool, manager: Any) -> None:
    app_config.api_proxy_enabled = bool(enabled)
    manager.apply_config()
    if not enabled:
        manager.api.set_proxy("")


def apply_proxies(manager: Any, *, api_enabled: bool, api_url: str, download_enabled: bool, download_url: str) -> tuple[str, str]:
    """Save both proxies (blank addresses fall back to the default) and apply them; returns the notice."""
    app_config.api_proxy_enabled = bool(api_enabled)
    app_config.api_proxy_url = str(api_url or "").strip() or DEFAULT_PROXY_URL
    app_config.download_proxy_enabled = bool(download_enabled)
    app_config.download_proxy_url = str(download_url or "").strip() or DEFAULT_PROXY_URL
    manager.apply_config()
    api_state = stext("on") if app_config.api_proxy_enabled else stext("off")
    download_state = stext("on") if app_config.download_proxy_enabled else stext("off")
    api, download = app_config.api_proxy_url, app_config.download_proxy_url
    return stext("proxy_applied"), tr(
        f"API: {api_state} {api}; Download: {download_state} {download}",
        f"API：{api_state} {api}；下载：{download_state} {download}",
        f"API: {api_state} {api}; ダウンロード: {download_state} {download}",
    )


def save_aria2(*, enabled: bool, url: str, token: str, remote: bool, remote_dir: str, mount_dir: str, http_base: str) -> None:
    app_config.aria2_rpc_enabled = bool(enabled)
    app_config.aria2_rpc_url = str(url or "").strip()
    app_config.aria2_rpc_token = str(token or "").strip()
    app_config.aria2_remote_enabled = bool(remote)
    app_config.aria2_remote_dir = str(remote_dir or "")
    app_config.aria2_remote_mount_dir = str(mount_dir or "")
    app_config.aria2_remote_http_base = str(http_base or "")


def set_auth_enabled(enabled: bool, manager: Any) -> None:
    """Turning sign-in off forgets the session (the saved credentials stay)."""
    app_config.auth_enabled = bool(enabled)
    if not enabled:
        manager.set_login(False)
        _bus().login_state_changed.emit(False)


def aria2_test_report(data: Any, error: str) -> tuple[bool, str, str]:
    """``(ok, title, message)`` for the reply to ``aria2.getVersion`` with the current settings."""
    import os

    if not data:
        return False, stext("aria2_not_reachable"), str(error or "")
    version = str((data.get("result") or {}).get("version", "") or "?")
    notes = [tr(f"aria2 {version} is reachable.", f"已连接 aria2 {version}。", f"aria2 {version} に接続しました。")]
    if app_config.aria2_remote_enabled:
        if not app_config.aria2_remote_dir:
            notes.append(stext("fill_in_the_download_folder_on"))
        mount = app_config.aria2_remote_mount_dir
        if mount and not os.path.isdir(mount):
            notes.append(tr(
                f"The local mount folder is not reachable now: {mount}",
                f"本机挂载路径当前不可访问：{mount}",
                f"マウント先に現在アクセスできません: {mount}",
            ))
        elif not mount and not app_config.aria2_remote_http_base:
            notes.append(stext("without_a_mount_folder_or_http"))
    return True, stext("connection_ok"), " ".join(notes)


def login_failed_text(message: str) -> str:
    return tr(f"✗ Login failed: {message}", f"✗ 登录失败: {message}", f"✗ ログイン失敗: {message}")


# ── search ───────────────────────────────────────────────────────────────────

SEARCH_SOURCES = ("iwara", "oreno3d")
SEARCH_LIMIT_RANGE = (1, 5000)
SEARCH_HISTORY_RANGE = (1, 100)
RESOLUTION_MODES = ("eager", "on_demand")
RESOLUTION_MODE_KEY = "search_iwara_resolution_mode_v1"
RESOLUTION_WORKERS_KEY = "search_iwara_resolution_workers_v1"
RESOLUTION_WORKERS_RANGE = (1, 8)
SEARCH_HISTORY_KEY = "search_history_v1"


def set_search_source(source: str) -> bool:
    source = source if source in SEARCH_SOURCES else "iwara"
    if source == app_config.search_default_source:
        return False
    app_config.search_default_source = source
    _bus().search_source_changed.emit(source)
    return True


def set_search_limit_count(text: Any) -> int:
    current = max(1, app_config.search_limit_count)
    value = parse_int(text, current=current, low=SEARCH_LIMIT_RANGE[0], high=SEARCH_LIMIT_RANGE[1])
    app_config.search_limit_count = value
    return value


def set_search_history_limit(value: Any) -> int:
    """A lower limit trims the saved history at once, so raising it later brings nothing back."""
    limit = clamp(value, *SEARCH_HISTORY_RANGE, default=app_config.search_history_limit)
    app_config.search_history_limit = limit
    raw = app_config.get_ui_value(SEARCH_HISTORY_KEY, "[]")
    try:
        history = json.loads(str(raw or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        history = []
    if isinstance(history, list) and len(history) > limit:
        app_config.set_ui_value(SEARCH_HISTORY_KEY, json.dumps(history[:limit], ensure_ascii=False, separators=(",", ":")))
    return limit


def resolution_mode() -> str:
    mode = str(app_config.get_ui_value(RESOLUTION_MODE_KEY, "eager") or "eager").strip().lower()
    return mode if mode in RESOLUTION_MODES else "eager"


def set_resolution_mode(mode: str) -> str:
    mode = mode if mode in RESOLUTION_MODES else "eager"
    app_config.set_ui_value(RESOLUTION_MODE_KEY, mode)
    return mode


def resolution_workers() -> int:
    return ui_int(RESOLUTION_WORKERS_KEY, 4, *RESOLUTION_WORKERS_RANGE)


def set_resolution_workers(value: Any) -> int:
    value = clamp(value, *RESOLUTION_WORKERS_RANGE, default=4)
    app_config.set_ui_value(RESOLUTION_WORKERS_KEY, value)
    return value


def tag_dictionary_result(result: object) -> tuple[bool, str, str, str]:
    """``(ok, status line, notice title, log line)`` for a tag dictionary update."""
    count, error = result if isinstance(result, tuple) and len(result) == 2 else (0, "")
    if error:
        message = tr(f"Tag dictionary update failed: {error}", f"标签词典更新失败：{error}", f"タグ辞書の更新に失敗：{error}")
        return False, stext("update_failed"), stext("tag_update_failed"), f"[Tags] {message}"
    status = tr(f"Loaded {count} tags", f"已加载 {count} 个标签", f"{count} 件のタグを読み込みました")
    log = tr(f"[Tags] Loaded {count} localized tags", f"[标签] 已加载 {count} 个多语言标签", f"[タグ] 多言語タグを{count}件読み込みました")
    return True, status, stext("tags_updated"), log


# ── subscriptions and covers ─────────────────────────────────────────────────

COVER_WORKERS_KEY = "cover_download_workers_v1"
COVER_WORKERS_RANGE = (1, 16)
REFRESH_WORKERS_KEY = "subscription_refresh_workers_v1"
REFRESH_WORKERS_RANGE = (1, 8)
INCREMENTAL_KEY = "subscription_incremental_refresh_v1"
REFRESH_INTERVAL_RANGE = (1, 1440)


def cover_workers() -> int:
    return ui_int(COVER_WORKERS_KEY, 6, *COVER_WORKERS_RANGE)


def set_cover_workers(value: Any) -> int:
    value = clamp(value, *COVER_WORKERS_RANGE, default=6)
    app_config.set_ui_value(COVER_WORKERS_KEY, value)
    return value


def refresh_workers() -> int:
    return ui_int(REFRESH_WORKERS_KEY, 3, *REFRESH_WORKERS_RANGE)


def set_refresh_workers(value: Any) -> int:
    value = clamp(value, *REFRESH_WORKERS_RANGE, default=3)
    app_config.set_ui_value(REFRESH_WORKERS_KEY, value)
    return value


def incremental_refresh() -> bool:
    return ui_bool(INCREMENTAL_KEY, True)


def set_incremental_refresh(enabled: bool) -> None:
    app_config.set_ui_value(INCREMENTAL_KEY, bool(enabled))


# ── appearance, tools, system ────────────────────────────────────────────────


def set_font_family(family: str) -> bool:
    from ..fonts import AUTO_FONT

    family = str(family or AUTO_FONT).strip()
    if family == str(app_config.ui_font_family or "").strip():
        return False
    app_config.ui_font_family = family
    _bus().font_changed.emit()
    return True


def set_ffmpeg_path(path: str) -> bool:
    """Save a typed or chosen ffmpeg path; True when it changed (check it again)."""
    path = str(path or "").strip().strip('"')
    if path == str(app_config.ffmpeg_path or ""):
        return False
    app_config.ffmpeg_path = path
    return True


def set_autostart(enabled: bool) -> str:
    """Launch at login; returns the error text ("" when it worked)."""
    from ...core import autostart

    try:
        autostart.set_enabled(bool(enabled))
    except OSError as exc:
        return str(exc)
    return ""


def backup_saved_text(count: int, path: str) -> str:
    return tr(f"Saved {count} files to {path}", f"已保存 {count} 个文件到 {path}", f"{count} 件を {path} に保存しました")


def restore_staged_text(count: int) -> str:
    return tr(
        f"{count} files staged. Restart IwaraTool to finish restoring.",
        f"已暂存 {count} 个文件，重启 IwaraTool 后完成恢复。",
        f"{count} 件を準備しました。IwaraTool を再起動すると復元が完了します。",
    )


FFMPEG_CHECKING, FFMPEG_MISSING, FFMPEG_OK = "checking", "missing", "ok"


def ffmpeg_report(info: Any, error: str, *, checking: bool = False) -> tuple[str, str, str]:
    """``(state, status line, details)`` for the located ffmpeg (``info`` from ``core.tools.ffmpeg.locate``)."""
    from ...core.tools.ffmpeg import program_at

    if checking:
        return FFMPEG_CHECKING, stext("checking"), ""
    configured = str(app_config.ffmpeg_path or "").strip()
    if info is None:
        details = (
            tr(f"Not usable: {configured}", f"无法使用：{configured}", f"使用できません: {configured}")
            if configured else (error if error != "ffmpeg was not found" else "")
        )
        return FFMPEG_MISSING, stext("ffmpeg_not_found"), details
    status = f"✓ ffmpeg {info.version}"
    if configured and program_at(configured, "ffmpeg") is None:
        status += stext("the_chosen_path_is_unusable_using")
    encoders = ["libx264"] if info.has_libx264 else []
    encoders += list(info.hardware_encoders)
    lines = [str(info.path), stext("h_264_encoders") + (", ".join(encoders) if encoders else stext("none"))]
    if info.ffprobe is None:
        lines.append(stext("ffprobe_not_found_media_info_is"))
    return FFMPEG_OK, status, "\n".join(lines)
