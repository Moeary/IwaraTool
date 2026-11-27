"""Repair Center: options, row states, summaries and log lines.

Both interfaces build their options, saved state, row labels and messages
here; colours are role names (``accent``, ``success``, ``warning``,
``danger``, ``neutral``) each interface maps to its palette.
"""
from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ...config import app_config
from ...i18n import tr
from .texts import repair_text

DEFAULT_TEMPLATE = "{YYYY-MM-DD}_{title}_{id}.mp4"
MODE_IN_PLACE, MODE_ORGANIZE = "in_place", "organize"
KEY_MODE, KEY_FOLDER, KEY_OUTPUT, KEY_TEMPLATE = "repair_mode", "repair_folder", "repair_output_folder", "repair_filename_template"

# Rows whose Iwara ID the user may type in (a finished repair is final).
MANUAL_ID_STATES = frozenset({"not_found", "ambiguous", "failed", "ready", "conflict", "skipped", ""})
MANUAL_ID_HINT_STATES = frozenset({"not_found", "ambiguous"})


# ── options and saved state ──────────────────────────────────────────────────


@dataclass(frozen=True)
class RepairForm:
    folder: str = ""
    mode: str = MODE_IN_PLACE
    output: str = ""
    template: str = ""
    rename: bool = True
    download_video: bool = False
    download_thumbnail: bool = True
    collect_nfo: bool = True
    add_to_history: bool = True

    @property
    def move_to_output(self) -> bool:
        return self.mode == MODE_ORGANIZE

    @property
    def effective_template(self) -> str:
        return self.template.strip() or DEFAULT_TEMPLATE

    @property
    def source_folder(self) -> str:
        source = self.folder.strip()
        return os.path.abspath(os.path.expanduser(source)) if source else ""

    @property
    def output_root(self) -> str:
        """Where organize mode moves files (the source folder when left blank); "" in place."""
        if not self.move_to_output:
            return ""
        custom = self.output.strip()
        if custom:
            return os.path.abspath(os.path.expanduser(custom))
        return self.source_folder

    def options(self) -> dict[str, Any]:
        return {
            "filename_template": self.effective_template,
            "output_root": self.output_root,
            "move_to_output": self.move_to_output,
            "rename": self.rename,
            "download_video": self.download_video,
            "download_thumbnail": self.download_thumbnail,
            "collect_nfo": self.collect_nfo,
            "add_to_history": self.add_to_history,
        }


def saved_form(default_template: str = "") -> RepairForm:
    """The last folder, mode, output and template (older saves without a mode stay in place)."""
    mode = MODE_ORGANIZE if str(app_config.get_ui_value(KEY_MODE, MODE_IN_PLACE) or "") == MODE_ORGANIZE else MODE_IN_PLACE
    folder = str(app_config.get_ui_value(KEY_FOLDER, "") or "")
    return RepairForm(
        folder=folder if folder and os.path.isdir(folder) else "",
        mode=mode,
        output=str(app_config.get_ui_value(KEY_OUTPUT, "") or ""),
        template=str(app_config.get_ui_value(KEY_TEMPLATE, "") or "") or default_template,
    )


def save_mode(mode: str) -> None:
    app_config.set_ui_value(KEY_MODE, MODE_ORGANIZE if mode == MODE_ORGANIZE else MODE_IN_PLACE)


def save_form(form: RepairForm, *, folder: bool = False) -> None:
    if folder:
        app_config.set_ui_value(KEY_FOLDER, form.source_folder)
    app_config.set_ui_value(KEY_OUTPUT, form.output.strip())
    app_config.set_ui_value(KEY_TEMPLATE, form.effective_template)
    save_mode(form.mode)


def mode_choices() -> list[tuple[str, str]]:
    return [(MODE_IN_PLACE, repair_text("rename_in_place")), (MODE_ORGANIZE, repair_text("organize_into_output_folder"))]


def mode_label(mode: str) -> str:
    return dict(mode_choices()).get(mode, mode)


def mode_hint(mode: str) -> str:
    if mode == MODE_ORGANIZE:
        return tr(
            "Videos and their sidecars are moved into the output folder. Existing targets and batch conflicts are skipped.",
            "视频及附属文件会剪切到输出目录；已有目标与批次内冲突均跳过。",
            "動画と関連ファイルを出力フォルダーへ移動します。既存の変更先や一括処理内の競合はスキップします。",
        )
    return tr(
        "Keep every video's current folder and apply only the filename segment. Covers and NFO files follow; existing targets are skipped.",
        "保留每个视频的所在目录，只应用规则的文件名段。封面与 NFO 跟随重命名；已有目标会跳过。",
        "各動画の現在のフォルダーを保ち、ファイル名部分のみ適用します。サムネイルと NFO も名前変更し、既存の変更先はスキップします。",
    )


def rule_name(entry: dict[str, Any]) -> str:
    return str(entry.get("name", "") or tr("Unnamed rule", "未命名规则", "名前なしルール"))


# ── rows ─────────────────────────────────────────────────────────────────────


def state_key(item: dict[str, Any]) -> str:
    key = str(item.get("status", "") or "")
    return "conflict" if key == "ready" and item.get("target_conflict") else key


def state_styles() -> dict[str, tuple[str, str | None]]:
    """State key -> (label, colour role); build once per batch of rows (``tr`` takes a lock)."""
    return {
        "": (tr("Unknown", "未知", "不明"), None),
        "ready": (tr("Ready", "待修复", "待機"), "accent"),
        "completed": (tr("Done", "已完成", "完了"), "success"),
        "not_found": (tr("Skip", "跳过", "スキップ"), "warning"),
        "ambiguous": (tr("Check", "待确认", "要確認"), "warning"),
        "failed": (tr("Failed", "失败", "失敗"), "danger"),
        "conflict": (tr("Conflict", "冲突", "競合"), "danger"),
        "skipped": (tr("Skip", "跳过", "スキップ"), "neutral"),
    }


def manual_id_hint() -> str:
    return tr("Double-click the Iwara ID cell to enter an ID or URL", "可双击 Iwara ID 列手动填写 ID 或链接",
              "Iwara ID 欄をダブルクリックして ID または URL を入力できます")


def row_detail(item: dict[str, Any], *, hint: str | None = None) -> str:
    detail = str(item.get("target_conflict", "") or item.get("message", "") or "")
    if state_key(item) in MANUAL_ID_HINT_STATES:
        hint = manual_id_hint() if hint is None else hint
        detail = f"{detail} · {hint}" if detail else hint
    return detail


def id_editable(item: dict[str, Any]) -> bool:
    return state_key(item) in MANUAL_ID_STATES


def repairable(items: list[dict[str, Any]]) -> bool:
    return any(item.get("status") == "ready" and not item.get("target_conflict") for item in items)


def column_labels() -> list[str]:
    return [repair_text("state"), repair_text("original_path"), "Iwara ID", repair_text("title"),
            repair_text("date"), repair_text("target_path"), repair_text("detail")]


def summary_counts(items: list[dict[str, Any]]) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for item in items:
        key = state_key(item) or "unknown"
        counts[key] = counts.get(key, 0) + 1
    skipped = counts.get("not_found", 0) + counts.get("ambiguous", 0) + counts.get("skipped", 0)
    return [
        (tr("Total", "共", "合計"), len(items)),
        (tr("Ready", "待修复", "待機"), counts.get("ready", 0)),
        (tr("Done", "已完成", "完了"), counts.get("completed", 0)),
        (tr("Skipped", "跳过", "スキップ"), skipped),
        (tr("Conflict/failed", "冲突/失败", "競合/失敗"), counts.get("conflict", 0) + counts.get("failed", 0)),
    ]


def merge_batch(by_path: dict[str, dict[str, Any]], batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add finished rows to ``by_path``; the rows that had a path."""
    rows = []
    for item in batch:
        path = str(item.get("path", "") or "")
        if path:
            by_path[path] = dict(item)
            rows.append(item)
    return rows


def index_items(items: list[Any]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    clean = [dict(item) for item in items if isinstance(item, dict)]
    return clean, {str(i.get("path", "") or ""): i for i in clean if str(i.get("path", "") or "")}


# ── status and log lines ─────────────────────────────────────────────────────


def idle_status() -> str:
    return repair_text("choose_a_folder_to_begin")


def scanning_status() -> str:
    return tr("Scanning files…", "正在扫描文件…", "ファイルを検索中…")


def stopping_status() -> str:
    return tr("Stopping…", "正在停止…", "停止中…")


def progress_status(scanning: bool, index: int, total: int) -> str:
    if scanning:
        return tr(f"Scan completed {index}/{total}", f"扫描完成 {index}/{total}", f"検索完了 {index}/{total}") if index == total else ""
    return tr(f"Processing {index}/{total}", f"处理中 {index}/{total}", f"処理中 {index}/{total}")


def activity_status(completed: int, total: int, active: int) -> tuple[str, str]:
    """``(status line, log line)`` while scanning."""
    queued = max(0, total - completed - active)
    return (
        tr(f"Scanning {completed}/{total} · active {active} · queued {queued}",
           f"扫描中 {completed}/{total} · 活跃 {active} · 排队 {queued}",
           f"検索中 {completed}/{total} · 実行中 {active} · 待機 {queued}"),
        tr(f"[Repair] scan progress: {completed}/{total}, active={active}, queued={queued}",
           f"[修复] 扫描进度：{completed}/{total}，活跃请求 {active}，排队 {queued}",
           f"[修復] 検索進捗: {completed}/{total}、実行中={active}、待機={queued}"),
    )


def scan_started_log(mode_text: str, folder: str, output_root: str) -> str:
    return tr(
        f"[Repair] scanning ({mode_text}): {folder}" + (f" -> {output_root}" if output_root else ""),
        f"[修复] 开始扫描（{mode_text}）：{folder}" + (f" → {output_root}" if output_root else ""),
        f"[修復] 検索開始（{mode_text}）: {folder}" + (f" -> {output_root}" if output_root else ""),
    )


def repair_started_log(count: int) -> str:
    return tr(f"[Repair] starting {count} files", f"[修复] 开始处理 {count} 个文件", f"[修復] {count} 件の処理を開始")


def scan_finished_log(items: list[dict[str, Any]]) -> str:
    ready = sum(1 for item in items if item.get("status") == "ready" and not item.get("target_conflict"))
    total = len(items)
    return tr(f"[Repair] scan complete: {ready}/{total} ready", f"[修复] 扫描完成：{ready}/{total} 个可修复",
              f"[修復] 検索完了: {ready}/{total} 件が修復可能")


def lookup_started_log(path: str, text: str) -> str:
    return tr(f"[Repair] looking up {path} by Iwara ID {text}", f"[修复] 按 Iwara ID {text} 重新查询：{path}",
              f"[修復] Iwara ID {text} で再検索: {path}")


def lookup_finished_log(path: str, item: dict[str, Any]) -> str:
    vid, title = item.get("video_id", ""), item.get("title", "")
    note = item.get("target_conflict") or item.get("message", "")
    return tr(f"[Repair] {path}: {vid} {title} — {note}", f"[修复] {path}：{vid} {title} — {note}",
              f"[修復] {path}: {vid} {title} — {note}")


def repair_finished_log(result: dict[str, Any]) -> str:
    g: Callable[[str], Any] = lambda key: result.get(key, 0)  # noqa: E731
    return tr(
        f"[Repair] finished: renamed={g('renamed')}, NFO={g('nfo')}, cover={g('thumbnail')}, skipped={g('skipped')}, failed={g('failed')}",
        f"[修复] 完成：重命名 {g('renamed')}，NFO {g('nfo')}，封面 {g('thumbnail')}，跳过 {g('skipped')}，失败 {g('failed')}",
        f"[修復] 完了: 名前変更={g('renamed')}, NFO={g('nfo')}, サムネイル={g('thumbnail')}, スキップ={g('skipped')}, 失敗={g('failed')}",
    )


def error_log(message: str) -> str:
    return tr(f"[Repair] error: {message}", f"[修复] 出错：{message}", f"[修復] エラー: {message}")


def lookup_status(state: str) -> str:
    return {
        "looking": tr("Looking up the video…", "正在查询视频…", "動画を検索中…"),
        "done": tr("Lookup finished", "查询完成", "検索完了"),
        "failed": tr("Lookup failed", "查询失败", "検索に失敗"),
        "stopped": tr("Lookup stopped", "已停止查询", "検索を停止しました"),
    }[state]


def warning(kind: str) -> tuple[str, str]:
    return {
        "folder": (tr("Folder not found", "文件夹不存在", "フォルダーが見つかりません"),
                   tr("Choose or drop a valid local folder first.", "请先选择或拖入有效的本地文件夹。",
                      "有効なローカルフォルダーを選択またはドロップしてください。")),
        "empty": (tr("Nothing to repair", "没有可修复的项目", "修復対象がありません"),
                  tr("Scan a folder first.", "请先扫描文件夹。", "先にフォルダーを検索してください。")),
        "invalid_id": (tr("Invalid Iwara ID", "Iwara ID 无效", "無効な Iwara ID"),
                       tr("Enter an Iwara video ID or a video link such as https://www.iwara.tv/video/<id>.",
                          "请输入 Iwara 视频 ID，或 https://www.iwara.tv/video/<id> 这样的视频链接。",
                          "Iwara 動画 ID、または https://www.iwara.tv/video/<id> のような動画リンクを入力してください。")),
    }[kind]


def failed_title() -> str:
    return tr("Repair failed", "修复失败", "修復に失敗")
