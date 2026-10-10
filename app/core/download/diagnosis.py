"""Answer "why was this video not downloaded?" from the queue, history, disk and the site."""

from __future__ import annotations

import os
from typing import Any

from ...config import app_config
from ...i18n import tr
from ..models import TaskStatus, status_label
from ..rules import rule_store
from ..subscription.automation import matches_rule_video


BLOCK = "block"  # this is why it was not (or will not be) downloaded
WARN = "warn"  # worth knowing, may delay or block a later download
INFO = "info"
OK = "ok"

_ACTIVE_STATUSES = frozenset(
    (TaskStatus.RESOLVING, TaskStatus.QUEUED_DOWNLOAD, TaskStatus.DOWNLOADING, TaskStatus.CANCELLING)
)


def _finding(level: str, text: str) -> dict[str, str]:
    return {"level": level, "text": text}


class DownloadDiagnosisMixin:
    """Answer "why was this video not downloaded?" from the queue, history, disk and the site."""

    def explain_not_downloaded(self, text: str, *, rule_id: str = "", online: bool = True) -> dict[str, Any]:
        """Every reason a video was skipped, failed or is still waiting, plus a one-line verdict.

        Local state (queue, history, files on disk, settings) is checked first;
        with ``online`` the video's page is fetched to check that it can be
        downloaded at all and that the selected rule lets it through.
        """

        video_id, error = self._diagnosis_video_id(text)
        result: dict[str, Any] = {"video_id": video_id, "title": "", "findings": [], "task_status": ""}
        if error:
            result["findings"].append(_finding(BLOCK, error))
            return self._diagnosis_verdict(result)
        findings: list[dict[str, str]] = result["findings"]
        findings.extend(self._diagnose_settings())
        findings.extend(self._diagnose_task(video_id, result))
        findings.extend(self._diagnose_local_copies(video_id, result))
        findings.extend(self._diagnose_subscription_state(video_id))
        if online:
            findings.extend(self._diagnose_online(video_id, rule_id, result))
        return self._diagnosis_verdict(result)

    def _diagnosis_video_id(self, text: str) -> tuple[str, str]:
        raw = str(text or "").strip()
        if not raw:
            return "", tr("Enter a video link or ID first.", "请先输入视频链接或 ID。", "動画のリンクまたは ID を入力してください。")
        parsed = self._parse_iwara_url(raw)
        if parsed is None:
            if "://" in raw or "/" in raw:
                return "", tr(
                    "This is not an Iwara video link.", "这不是 Iwara 的视频链接。", "Iwara の動画リンクではありません。",
                )
            return raw, ""
        kind, value = parsed
        if kind != "video":
            return "", tr(
                "This checks one video. Author and playlist links show what they contain when you click Download.",
                "此功能针对单个视频。作者 / 播放列表链接请直接点“下载”，会先列出其中的作品与已有、符合规则的数量。",
                "1 本の動画を調べます。作者/プレイリストのリンクは「ダウンロード」で内容を一覧できます。",
            )
        return str(value), ""

    def _diagnose_settings(self) -> list[dict[str, str]]:
        findings: list[dict[str, str]] = []
        if app_config.mark_submitted_as_downloaded or not app_config.download_video_file:
            findings.append(_finding(BLOCK, tr(
                "Submitted links are only marked as downloaded (Settings → Download): no video file is saved.",
                "当前设置为“提交的链接只标记为已下载”（设置 → 下载），不会保存视频文件。",
                "送信したリンクは保存済みとして記録するだけの設定です（設定 → ダウンロード）。動画ファイルは保存されません。",
            )))
        if not self._download_schedule_allows_start():
            findings.append(_finding(WARN, tr(
                f"Outside the download time window ({app_config.download_schedule_start}–{app_config.download_schedule_end}): queued tasks wait until it opens.",
                f"当前不在下载时段（{app_config.download_schedule_start}–{app_config.download_schedule_end}），排队的任务会等到时段开始才启动。",
                f"ダウンロード時間帯（{app_config.download_schedule_start}–{app_config.download_schedule_end}）の外です。待機中のタスクは時間帯まで開始しません。",
            )))
        return findings

    def _diagnose_task(self, video_id: str, result: dict[str, Any]) -> list[dict[str, str]]:
        with self._lock:
            task_id = self._task_id_by_video_id.get(video_id.lower(), "")
            task = self._tasks.get(task_id) if task_id else None
            if task is None:
                return []
            status, error, title = task.status, str(task.error_msg or ""), str(task.title or "")
            file_path = str(task.file_path or "")
            position = self._queue_position_locked(task_id)
            active, limit = len(self._active_task_ids), app_config.max_concurrent
        result["task_status"] = status.value
        if title and title != video_id:
            result["title"] = title
        label = status_label(status)
        if status in (TaskStatus.SKIPPED, TaskStatus.FAILED, TaskStatus.CANCELLED):
            return self._ended_task_findings(status, error)
        if status == TaskStatus.COMPLETED:
            return [_finding(OK, tr(f"Downloaded in this session: {file_path}", f"本次运行中已下载完成：{file_path}", f"今回の実行で保存済み: {file_path}"))]
        if status == TaskStatus.QUEUED_META:
            return [_finding(INFO, tr(
                f"Waiting in the queue (position {position}); {active}/{limit} tasks are running.",
                f"正在排队（第 {position} 位），当前运行 {active}/{limit} 个任务。",
                f"キューで待機中（{position} 番目）。実行中 {active}/{limit}。",
            ))]
        if status in _ACTIVE_STATUSES:
            return [_finding(INFO, tr(f"In progress: {label}", f"正在处理：{label}", f"処理中: {label}"))]
        return []

    @staticmethod
    def _ended_task_findings(status: TaskStatus, error: str) -> list[dict[str, str]]:
        if status == TaskStatus.SKIPPED:
            reason = tr(f"Skipped by the download rule: {error}", f"被下载规则跳过：{error}", f"ルールでスキップ: {error}")
        elif status == TaskStatus.FAILED:
            reason = tr(f"The download failed: {error}", f"下载失败：{error}", f"ダウンロード失敗: {error}")
        else:
            reason = tr(f"The task was stopped: {error}", f"任务已中断：{error}", f"タスクは中断されました: {error}")
        return [
            _finding(BLOCK, reason),
            _finding(INFO, tr(
                "The task is still in the task list, so submitting the link again is ignored as a duplicate: use Retry, or remove the task first.",
                "该任务仍在任务列表中，再次提交同一链接会被当作重复而忽略：请用“重试”，或先移除该任务。",
                "タスクが一覧に残っているため、同じリンクを再送信しても重複として無視されます。「再試行」を使うか、先にタスクを削除してください。",
            )),
        ]

    def _queue_position_locked(self, task_id: str) -> int:
        try:
            return list(self._queued_meta_ids).index(task_id) + 1
        except ValueError:
            return 0

    def _diagnose_local_copies(self, video_id: str, result: dict[str, Any]) -> list[dict[str, str]]:
        findings: list[dict[str, str]] = []
        record = self.history.get_record(video_id)
        recorded_path = ""
        if record:
            result["title"] = result["title"] or str(record.get("title", "") or "")
            recorded_path = str(record.get("file_path", "") or "")
            when = str(record.get("downloaded_at", "") or "")[:16]
            if recorded_path and os.path.isfile(recorded_path):
                findings.append(_finding(BLOCK, tr(
                    f"Already downloaded ({when}): {recorded_path}",
                    f"已经下载过（{when}）：{recorded_path}",
                    f"保存済みです（{when}）: {recorded_path}",
                )))
            else:
                findings.append(_finding(WARN, tr(
                    f"History says it was downloaded ({when}), but the file is gone. Subscriptions treat it as downloaded and will not fetch it again; submitting the link downloads it anew.",
                    f"历史记录显示 {when} 下载过，但文件已不在原处。订阅会把它当作已下载、不再自动下载；直接提交链接会重新下载。",
                    f"履歴では {when} に保存済みですが、ファイルがありません。購読は保存済みとみなして再取得しません。リンクを送信すると再ダウンロードします。",
                )))
        existing = self._get_existing_file_index().get(video_id.lower(), "")
        if existing and os.path.normcase(existing) != os.path.normcase(recorded_path):
            level = BLOCK if app_config.skip_existing_files else INFO
            findings.append(_finding(level, tr(
                f"The download folder already has a file with this ID: {existing}"
                + (" (\"Skip existing files\" is on)" if app_config.skip_existing_files else ""),
                f"下载文件夹里已有含此 ID 的文件：{existing}"
                + ("（已开启“跳过已存在的文件”）" if app_config.skip_existing_files else ""),
                f"保存先にこの ID のファイルがあります: {existing}"
                + ("（「既存ファイルをスキップ」が有効）" if app_config.skip_existing_files else ""),
            )))
        return findings

    def _diagnose_subscription_state(self, video_id: str) -> list[dict[str, str]]:
        try:
            items = self.subscriptions.get_items_by_video_ids([video_id])
        except Exception:
            return []
        for item in items:
            reason = str(item.get("download_reason", "") or "")
            if str(item.get("download_state", "") or "") and reason:
                return [_finding(WARN, tr(
                    f"The last subscription check found it unavailable: {reason}",
                    f"订阅上次检查时判定为不可下载：{reason}",
                    f"前回の購読チェックで保存不可と判定: {reason}",
                ))]
        return []

    def _diagnose_online(self, video_id: str, rule_id: str, result: dict[str, Any]) -> list[dict[str, str]]:
        video_info, err = self._api_call("get_video_info", video_id)
        if not video_info:
            return [_finding(BLOCK, tr(f"The site did not return the video: {err}", f"站点没有返回这个视频：{err}", f"サイトが動画を返しませんでした: {err}"))]
        result["title"] = str(video_info.get("title", "") or "") or result["title"]
        unavailable = self._video_unavailable_reason(video_info)
        if unavailable:
            return [_finding(BLOCK, unavailable)]
        rule_id = self._normalize_rule_id(rule_id)
        rule = rule_store.find(rule_id) or {}
        name = str(rule.get("name", "") or rule_id)
        passed, reason = matches_rule_video(video_info, self._rule_payload_for_id(rule_id))
        if not passed:
            return [_finding(BLOCK, tr(
                f"The rule \"{name}\" filters it out: {reason}",
                f"不符合下载规则“{name}”：{reason}",
                f"ルール「{name}」で除外されます: {reason}",
            ))]
        return [_finding(OK, tr(
            f"The site offers a file and the rule \"{name}\" lets it through.",
            f"站点提供可下载文件，且符合下载规则“{name}”。",
            f"サイトにファイルがあり、ルール「{name}」も通過します。",
        ))]

    @staticmethod
    def _diagnosis_verdict(result: dict[str, Any]) -> dict[str, Any]:
        findings = result["findings"]
        blocking = [f for f in findings if f["level"] == BLOCK]
        status = result.get("task_status", "")
        if blocking:
            verdict = blocking[0]["text"]
        elif status in {s.value for s in _ACTIVE_STATUSES | {TaskStatus.QUEUED_META}}:
            verdict = next((f["text"] for f in findings if f["level"] == INFO), "")
        elif status == TaskStatus.COMPLETED.value:
            verdict = next((f["text"] for f in findings if f["level"] == OK), "")
        else:
            verdict = tr(
                "Nothing stops this video: it can be downloaded now.",
                "没有发现阻止下载的原因，现在就可以下载。",
                "妨げる要因は見つかりません。今すぐ保存できます。",
            )
        result["verdict"] = verdict
        result["can_download"] = not blocking and not status
        return result
