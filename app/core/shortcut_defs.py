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
SCOPE_HOME = "home"
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
        SCOPE_HOME: tr("Home", "首页", "ホーム"),
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
        A("nav_home", SCOPE_GLOBAL, "Ctrl+1", tr("Go to Home", "前往首页", "ホームへ移動")),
        A("nav_subscriptions", SCOPE_GLOBAL, "Ctrl+2", tr("Go to Subscriptions", "前往订阅页", "購読へ移動")),
        A("nav_search", SCOPE_GLOBAL, "Ctrl+3", tr("Go to Search", "前往搜索", "検索へ移動")),
        A("nav_download", SCOPE_GLOBAL, "Ctrl+4", tr("Go to Download Hub", "前往下载工作台", "ダウンロードハブへ移動")),
        A("nav_repair", SCOPE_GLOBAL, "Ctrl+5", tr("Go to Repair Center", "前往修复中心", "修復センターへ移動")),
        A("nav_history", SCOPE_GLOBAL, "Ctrl+6", tr("Go to History", "前往历史记录", "履歴へ移動")),
        A("nav_rules", SCOPE_GLOBAL, "Ctrl+7", tr("Go to Rules", "前往下载规则", "ルールへ移動")),
        A("nav_settings", SCOPE_GLOBAL, "Ctrl+8", tr("Go to Settings", "前往应用设置", "設定へ移動")),
        A("nav_next", SCOPE_GLOBAL, "Ctrl+Tab", tr("Next page", "下一个页面", "次のページ")),
        A("nav_prev", SCOPE_GLOBAL, "Ctrl+Shift+Tab", tr("Previous page", "上一个页面", "前のページ")),
        A("nav_back", SCOPE_GLOBAL, "Esc", tr(
            "Back (closes a menu or clears the selection first; the mouse back button also works)",
            "返回上一处（先关闭菜单或取消选择；鼠标侧键同样可用）",
            "戻る（先にメニューを閉じるか選択を解除。マウスの戻るボタンも使えます）",
        )),
        A("focus_search", SCOPE_GLOBAL, "Ctrl+K", tr("Quick search (go to Search and type)", "快速搜索（跳到搜索页并输入）", "クイック検索（検索ページで入力）")),
        A("quick_download", SCOPE_GLOBAL, "Ctrl+Alt+V", tr("Paste the clipboard link and download it", "粘贴剪贴板链接并下载", "クリップボードのリンクを貼り付けて保存")),
        A("open_download_folder", SCOPE_GLOBAL, "Ctrl+Shift+O", tr("Open the download folder", "打开下载文件夹", "保存フォルダーを開く")),
        A("rating_cycle", SCOPE_GLOBAL, "Ctrl+Shift+R", tr("Cycle the content filter (All / SFW / NSFW)", "切换内容分级（全部 / SFW / NSFW）", "コンテンツ区分を切り替え（すべて/SFW/NSFW）")),
        A("card_size_up", SCOPE_GLOBAL, "Ctrl+=", tr("Larger covers", "放大封面", "カバーを大きく")),
        A("card_size_down", SCOPE_GLOBAL, "Ctrl+-", tr("Smaller covers", "缩小封面", "カバーを小さく")),
        A("card_size_reset", SCOPE_GLOBAL, "Ctrl+0", tr("Default cover size", "恢复默认封面大小", "カバーサイズを初期化")),
        A("toggle_theme", SCOPE_GLOBAL, "Ctrl+Shift+L", tr("Toggle dark mode", "切换黑夜模式", "ダークモード切替")),
        A("toggle_fullscreen", SCOPE_GLOBAL, "F11", tr("Toggle full screen", "切换全屏", "全画面の切り替え")),
        A("shortcut_help", SCOPE_GLOBAL, "F1", tr("Show all keyboard shortcuts", "显示全部快捷键", "ショートカット一覧を表示")),
        A("quit_app", SCOPE_GLOBAL, "Ctrl+Q", tr("Exit IwaraTool", "退出 IwaraTool", "IwaraTool を終了")),

        A("home_refresh", SCOPE_HOME, "F5", tr("Refresh the Home feeds", "刷新首页内容", "ホームを更新")),
        A("home_back", SCOPE_HOME, "Alt+Left", tr("Back (to the feed when there is no history)", "返回（无历史时回到首页）", "戻る（履歴がなければホームへ）")),
        A("home_customize", SCOPE_HOME, "Ctrl+E", tr("Customize the Home rows", "自定义首页栏目", "ホームの欄をカスタマイズ")),
        A("home_top", SCOPE_HOME, "Ctrl+Home", tr("Scroll to the top", "回到顶部", "先頭へスクロール")),
        A("home_bottom", SCOPE_HOME, "Ctrl+End", tr("Scroll to the bottom", "滚动到底部", "末尾へスクロール")),
        A("home_fold_all", SCOPE_HOME, "Ctrl+Shift+Up", tr("Fold every row", "折叠全部栏目", "すべての欄を折りたたむ")),
        A("home_unfold_all", SCOPE_HOME, "Ctrl+Shift+Down", tr("Unfold every row", "展开全部栏目", "すべての欄を展開")),
        A("detail_like", SCOPE_HOME, "Ctrl+L", tr("Like / un-like the open post", "给打开的作品点赞 / 取消点赞", "開いている投稿にいいね/取消")),
        A("detail_play", SCOPE_HOME, "Ctrl+P", tr("Play the open post", "播放打开的作品", "開いている投稿を再生")),
        A("detail_download", SCOPE_HOME, "Ctrl+D", tr("Download the open post", "下载打开的作品", "開いている投稿を保存")),
        A("detail_open_browser", SCOPE_HOME, "Ctrl+O", tr("Open the post in the browser", "在浏览器打开作品", "投稿をブラウザーで開く")),
        A("detail_copy_link", SCOPE_HOME, "Ctrl+Shift+C", tr("Copy the post's link", "复制作品链接", "投稿のリンクをコピー")),
        A("detail_author_page", SCOPE_HOME, "Ctrl+Shift+A", tr("Open the post's author page", "打开作品的作者页", "投稿の作者ページを開く")),

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
        A("search_prev_page", SCOPE_SEARCH, "Alt+Left", tr("Previous page of results", "上一页结果", "前の結果ページ")),
        A("search_next_page", SCOPE_SEARCH, "Alt+Right", tr("Next page of results", "下一页结果", "次の結果ページ")),
        A("search_toggle_view", SCOPE_SEARCH, "Ctrl+Shift+G", tr("Switch grid / list view", "切换网格 / 列表视图", "グリッド/リスト表示の切り替え")),
        A("search_toggle_controls", SCOPE_SEARCH, "Ctrl+Shift+F", tr("Show / hide the search controls", "展开 / 收起搜索区", "検索欄の表示/非表示")),
        A("search_reset", SCOPE_SEARCH, "Ctrl+Shift+Backspace", tr("Reset the search", "重置搜索", "検索をリセット")),

        A("sub_add", SCOPE_SUBSCRIPTIONS, "Ctrl+N", tr("Add a subscription", "添加订阅", "購読を追加")),
        A("sub_refresh", SCOPE_SUBSCRIPTIONS, "F5", tr("Refresh the current source", "刷新当前订阅源", "現在の購読元を更新")),
        A("sub_refresh_all", SCOPE_SUBSCRIPTIONS, "Ctrl+F5", tr("Refresh all sources", "刷新全部订阅源", "すべての購読元を更新")),
        A("sub_download_selected", SCOPE_SUBSCRIPTIONS, "Ctrl+D", tr("Download the selection", "下载选中项", "選択をダウンロード")),
        A("sub_download_new", SCOPE_SUBSCRIPTIONS, "Ctrl+Shift+D", tr("Download new videos", "下载新增视频", "新着動画をダウンロード")),
        A("sub_back", SCOPE_SUBSCRIPTIONS, "Alt+Left", tr("Back (to the list of subscriptions when there is no history)", "返回（无历史时回到订阅列表）", "戻る（履歴がなければ購読の一覧へ）")),
        A("sub_select_all", SCOPE_SUBSCRIPTIONS, "Ctrl+A", tr("Select every video in the grid", "全选网格中的视频", "グリッドの動画をすべて選択")),
        A("sub_clear_selection", SCOPE_SUBSCRIPTIONS, "Ctrl+Shift+A", tr("Clear the selection (Esc also does this first)", "取消选择（Esc 也会先取消选择）", "選択を解除（Escでも先に解除）")),
        A("sub_toggle_view", SCOPE_SUBSCRIPTIONS, "Ctrl+Shift+T", tr("Switch overview / table view", "切换总览 / 经典表格", "概要/表形式の切り替え")),
        A("sub_focus_filter", SCOPE_SUBSCRIPTIONS, "Ctrl+F", tr("Focus the filter box", "聚焦筛选框", "絞り込み欄へ移動")),

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


_default_keys_cache: dict[str, str] | None = None


def _default_keys() -> dict[str, str]:
    """``{action id: default key}``; defaults do not depend on the language, so build once.

    ``key_for`` is called for every tooltip hint and every shortcut refresh;
    rebuilding the translated catalogue each time was needlessly slow.
    """

    global _default_keys_cache
    if _default_keys_cache is None:
        _default_keys_cache = {action.id: action.default for action in all_actions()}
    return _default_keys_cache


def key_for(action_id: str) -> str:
    """Effective shortcut for ``action_id`` (``""`` when unassigned)."""
    default = _default_keys().get(action_id)
    if default is None:
        return ""
    overrides = load_overrides()
    if action_id in overrides:
        return normalize(overrides[action_id])
    return normalize(default)


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
