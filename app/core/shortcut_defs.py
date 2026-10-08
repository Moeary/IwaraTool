"""Keyboard shortcut catalogue: actions, defaults, user overrides and conflicts.

Only data and rules live here; ``app.ui.shortcuts`` turns them into QShortcuts.
Overrides are stored as JSON in ``shortcut_overrides`` (``{action_id: "Ctrl+K"}``,
an empty string meaning "unassigned").
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from PySide6.QtGui import QKeySequence

from ..config import app_config
from ..i18n import tr
from ..logging_setup import get_logger

logger = get_logger(__name__)

SCOPE_GLOBAL = "global"
SCOPE_DOWNLOAD = "download"
SCOPE_SEARCH = "search"
SCOPE_SUBSCRIPTIONS = "subscriptions"
SCOPE_HISTORY = "history"
SCOPE_REPAIR = "repair"
SCOPE_RULES = "rules"
SCOPE_SETTINGS = "settings"
SCOPE_PLAYER = "player"


@dataclass(frozen=True)
class ShortcutAction:
    id: str
    scope: str
    default: str
    label: str


def scope_titles() -> dict[str, str]:
    return {
        SCOPE_GLOBAL: tr("Anywhere in the app", "全局（任何页面）", "どこでも（グローバル）"),
        SCOPE_DOWNLOAD: tr("Download Hub", "下载工作台", "ダウンロードハブ"),
        SCOPE_SEARCH: tr("Search", "搜索", "検索"),
        SCOPE_SUBSCRIPTIONS: tr("Subscriptions", "订阅页", "購読"),
        SCOPE_HISTORY: tr("History", "历史记录", "履歴"),
        SCOPE_REPAIR: tr("Repair Center", "修复中心", "修復センター"),
        SCOPE_RULES: tr("Rules", "下载规则", "ルール"),
        SCOPE_SETTINGS: tr("Settings", "应用设置", "設定"),
        SCOPE_PLAYER: tr("Video player window", "视频播放窗口", "動画プレーヤー"),
    }


def all_actions() -> tuple[ShortcutAction, ...]:
    """The catalogue, built on demand so labels follow the UI language."""
    A = ShortcutAction
    return (
        A("nav_download", SCOPE_GLOBAL, "Ctrl+1", tr("Go to Download Hub", "前往下载工作台", "ダウンロードハブへ移動")),
        A("nav_search", SCOPE_GLOBAL, "Ctrl+2", tr("Go to Search", "前往搜索", "検索へ移動")),
        A("nav_subscriptions", SCOPE_GLOBAL, "Ctrl+3", tr("Go to Subscriptions", "前往订阅页", "購読へ移動")),
        A("nav_history", SCOPE_GLOBAL, "Ctrl+4", tr("Go to History", "前往历史记录", "履歴へ移動")),
        A("nav_repair", SCOPE_GLOBAL, "Ctrl+5", tr("Go to Repair Center", "前往修复中心", "修復センターへ移動")),
        A("nav_rules", SCOPE_GLOBAL, "Ctrl+6", tr("Go to Rules", "前往下载规则", "ルールへ移動")),
        A("nav_settings", SCOPE_GLOBAL, "Ctrl+7", tr("Go to Settings", "前往应用设置", "設定へ移動")),
        A("quit_app", SCOPE_GLOBAL, "Ctrl+Q", tr("Exit IwaraTool", "退出 IwaraTool", "IwaraTool を終了")),

        A("download_submit", SCOPE_DOWNLOAD, "Ctrl+Return", tr("Submit the links", "提交链接下载", "リンクを送信")),
        A("download_paste_submit", SCOPE_DOWNLOAD, "Ctrl+Shift+V", tr("Paste from clipboard and submit", "粘贴剪贴板并提交", "クリップボードを貼り付けて送信")),
        A("tasks_focus_search", SCOPE_DOWNLOAD, "Ctrl+F", tr("Search tasks", "搜索任务", "タスクを検索")),
        A("tasks_retry_failed", SCOPE_DOWNLOAD, "Ctrl+R", tr("Retry all failed tasks", "重试所有失败任务", "失敗したタスクをすべて再試行")),
        A("tasks_clear_done", SCOPE_DOWNLOAD, "Ctrl+Shift+Delete", tr("Clear completed tasks", "清除已完成任务", "完了タスクを消去")),

        A("search_focus", SCOPE_SEARCH, "Ctrl+F", tr("Focus the search box", "聚焦搜索框", "検索ボックスへ移動")),
        A("search_run", SCOPE_SEARCH, "Ctrl+Return", tr("Run the search", "执行搜索", "検索を実行")),
        A("search_queue", SCOPE_SEARCH, "Ctrl+D", tr("Download the selection", "下载选中项", "選択をダウンロード")),
        A("search_preview", SCOPE_SEARCH, "Ctrl+P", tr("Play / preview the selection", "播放 / 预览选中项", "選択を再生 / プレビュー")),
        A("search_open_page", SCOPE_SEARCH, "Ctrl+O", tr("Open the selection in the browser", "在浏览器打开选中项", "選択をブラウザーで開く")),

        A("sub_add", SCOPE_SUBSCRIPTIONS, "Ctrl+N", tr("Add a subscription", "添加订阅", "購読を追加")),
        A("sub_refresh", SCOPE_SUBSCRIPTIONS, "F5", tr("Refresh the current source", "刷新当前订阅源", "現在の購読元を更新")),
        A("sub_refresh_all", SCOPE_SUBSCRIPTIONS, "Ctrl+F5", tr("Refresh all sources", "刷新全部订阅源", "すべての購読元を更新")),
        A("sub_download_selected", SCOPE_SUBSCRIPTIONS, "Ctrl+D", tr("Download the selection", "下载选中项", "選択をダウンロード")),
        A("sub_download_new", SCOPE_SUBSCRIPTIONS, "Ctrl+Shift+D", tr("Download new videos", "下载新增视频", "新着動画をダウンロード")),

        A("history_focus_search", SCOPE_HISTORY, "Ctrl+F", tr("Focus the filter box", "聚焦搜索框", "検索ボックスへ移動")),
        A("history_open_file", SCOPE_HISTORY, "Ctrl+Return", tr("Open the video file", "打开视频文件", "動画ファイルを開く")),
        A("history_open_folder", SCOPE_HISTORY, "Ctrl+Shift+Return", tr("Open the containing folder", "打开所在文件夹", "保存フォルダーを開く")),
        A("history_preview", SCOPE_HISTORY, "Ctrl+P", tr("Play / preview the selection", "播放 / 预览选中项", "選択を再生 / プレビュー")),
        A("history_remove", SCOPE_HISTORY, "Delete", tr("Remove the selected records", "移除选中记录", "選択した履歴を削除")),
        A("history_export", SCOPE_HISTORY, "Ctrl+E", tr("Export the visible rows to CSV", "导出当前列表为 CSV", "表示中の行を CSV に出力")),
        A("history_reload", SCOPE_HISTORY, "F5", tr("Reload history", "重新加载历史", "履歴を再読み込み")),

        A("repair_scan", SCOPE_REPAIR, "F5", tr("Scan the folder", "扫描文件夹", "フォルダーをスキャン")),
        A("repair_apply", SCOPE_REPAIR, "Ctrl+Return", tr("Start the repair", "开始修复", "修復を開始")),

        A("rules_new", SCOPE_RULES, "Ctrl+N", tr("New rule", "新建规则", "新規ルール")),
        A("rules_save", SCOPE_RULES, "Ctrl+S", tr("Save the rule", "保存规则", "ルールを保存")),
        A("rules_delete", SCOPE_RULES, "Ctrl+Delete", tr("Delete the rule", "删除规则", "ルールを削除")),

        A("settings_save", SCOPE_SETTINGS, "Ctrl+S", tr("Save all settings", "保存所有设置", "すべて保存")),

        A("player_play_pause", SCOPE_PLAYER, "Space", tr("Play / pause", "播放 / 暂停", "再生 / 一時停止")),
        A("player_seek_back", SCOPE_PLAYER, "Left", tr("Seek back 5 s", "后退 5 秒", "5秒戻る")),
        A("player_seek_forward", SCOPE_PLAYER, "Right", tr("Seek forward 5 s (hold for 2x speed)", "前进 5 秒（按住倍速播放）", "5秒進む（長押しで倍速）")),
        A("player_volume_up", SCOPE_PLAYER, "Up", tr("Volume up", "音量增大", "音量を上げる")),
        A("player_volume_down", SCOPE_PLAYER, "Down", tr("Volume down", "音量减小", "音量を下げる")),
        A("player_mute", SCOPE_PLAYER, "M", tr("Mute", "静音", "ミュート")),
        A("player_fullscreen", SCOPE_PLAYER, "F", tr("Toggle fullscreen", "切换全屏", "全画面の切り替え")),
        A("player_exit_fullscreen", SCOPE_PLAYER, "Esc", tr("Leave fullscreen", "退出全屏", "全画面を終了")),
        A("player_speed_up", SCOPE_PLAYER, "]", tr("Faster", "加快播放速度", "再生速度を上げる")),
        A("player_speed_down", SCOPE_PLAYER, "[", tr("Slower", "减慢播放速度", "再生速度を下げる")),
    )


def actions_by_id() -> dict[str, ShortcutAction]:
    return {action.id: action for action in all_actions()}


def normalize(sequence: str) -> str:
    """Canonical portable text for ``sequence``; ``""`` when empty or invalid."""
    text = str(sequence or "").strip()
    if not text:
        return ""
    parsed = QKeySequence(text, QKeySequence.SequenceFormat.PortableText)
    return parsed.toString(QKeySequence.SequenceFormat.PortableText) if not parsed.isEmpty() else ""


def load_overrides() -> dict[str, str]:
    try:
        raw = json.loads(str(app_config.shortcut_overrides or "{}"))
    except (TypeError, ValueError):
        logger.warning("Ignoring unreadable shortcut_overrides", exc_info=True)
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(key): str(value) for key, value in raw.items() if isinstance(value, str)}


def _save_overrides(overrides: dict[str, str]) -> None:
    app_config.shortcut_overrides = json.dumps(overrides, ensure_ascii=False, sort_keys=True)


def key_for(action_id: str) -> str:
    """Effective shortcut for ``action_id`` (``""`` when unassigned)."""
    action = actions_by_id().get(action_id)
    if action is None:
        return ""
    overrides = load_overrides()
    if action_id in overrides:
        return normalize(overrides[action_id])
    return normalize(action.default)


def scopes_overlap(first: str, second: str) -> bool:
    """Whether two actions can be live at the same time."""
    if first == second:
        return True
    return SCOPE_PLAYER not in (first, second) and SCOPE_GLOBAL in (first, second)


def find_conflict(action_id: str, sequence: str) -> ShortcutAction | None:
    """Another action that already uses ``sequence`` in an overlapping scope."""
    sequence = normalize(sequence)
    if not sequence:
        return None
    catalogue = actions_by_id()
    action = catalogue.get(action_id)
    if action is None:
        return None
    for other in catalogue.values():
        if other.id == action_id or not scopes_overlap(action.scope, other.scope):
            continue
        if key_for(other.id) == sequence:
            return other
    return None


def set_key(action_id: str, sequence: str) -> ShortcutAction | None:
    """Assign ``sequence``; returns the conflicting action (and changes nothing) if any."""
    if action_id not in actions_by_id():
        raise KeyError(action_id)
    sequence = normalize(sequence)
    conflict = find_conflict(action_id, sequence)
    if conflict is not None:
        return conflict
    overrides = load_overrides()
    overrides[action_id] = sequence
    _save_overrides(overrides)
    return None


def reset_key(action_id: str) -> ShortcutAction | None:
    """Back to the default; refused (returning the conflicting action) if it is taken."""
    action = actions_by_id().get(action_id)
    if action is None:
        raise KeyError(action_id)
    conflict = find_conflict(action_id, action.default)
    if conflict is not None:
        return conflict
    overrides = load_overrides()
    overrides.pop(action_id, None)
    _save_overrides(overrides)
    return None


def reset_all() -> None:
    _save_overrides({})
