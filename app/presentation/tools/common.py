"""What the tools share: the tool list, sizes and clocks, the ffmpeg-missing text, file states."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ...i18n import tr

TOOL_KEY = "tools_current_v1"
VIDEO_EXTENSIONS = (".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v", ".flv", ".wmv", ".ts", ".mpg", ".mpeg")


@dataclass(frozen=True)
class ToolInfo:
    key: str
    title: str
    summary: str


def tools() -> tuple[ToolInfo, ...]:
    """Every tool in list order; built on demand so labels follow the language."""
    return (
        ToolInfo("compress", tr("Upload Compressor", "上传压缩", "アップロード用圧縮"), tr(
            "Fit a video under Iwara's 300 / 600 MB limit as H.264 + AAC MP4, at least 720p.",
            "把视频压到 Iwara 的 300 / 600MB 上限以内：H.264 + AAC、MP4，不低于 720p。",
            "Iwara の 300 / 600 MB 上限に収まるよう H.264 + AAC の MP4（720p 以上）に変換します。")),
        ToolInfo("covers", tr("Cover Preview", "封面预览", "カバーのプレビュー"), tr(
            "See the 16 screenshots Iwara will offer as the cover before uploading.",
            "上传前预览 Iwara 会给出的 16 张封面截图。",
            "アップロード前に Iwara が提示する 16 枚のカバー候補を確認します。")),
    )


def format_bytes(size: float) -> str:
    """Decimal units, the way Iwara's upload limit is stated."""
    size = float(size or 0)
    for unit in ("B", "KB", "MB"):
        if size < 1000:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1000
    return f"{size:.2f} GB"


def format_clock(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def clean_path(text: str) -> str:
    """A typed or pasted path without surrounding blanks and quotes."""
    return str(text or "").strip().strip('"')


def browse_start(text: str, fallback: str) -> str:
    """Where a file dialog opens: the typed file's folder, the typed folder, or ``fallback``."""
    start = clean_path(text) or str(fallback or "")
    return str(Path(start).parent if start and Path(start).is_file() else start)


def ffmpeg_missing_title() -> str:
    return tr("ffmpeg not found", "未找到 ffmpeg", "ffmpeg が見つかりません")


def ffmpeg_missing_text(error: str = "", *, no_h264: bool = False) -> str:
    text = tr(
        "This tool runs the ffmpeg program. Install it (macOS: brew install ffmpeg; Windows: winget install ffmpeg) "
        "or choose ffmpeg / ffmpeg.exe under Settings → Tools.",
        "此工具需要调用 ffmpeg 程序。请先安装（macOS：brew install ffmpeg；Windows：winget install ffmpeg），"
        "或在“设置 → 工具”中选择 ffmpeg / ffmpeg.exe 的位置。",
        "このツールは ffmpeg を使います。インストールするか（macOS: brew install ffmpeg / Windows: winget install ffmpeg）、"
        "設定 → ツールで ffmpeg / ffmpeg.exe を選択してください。",
    )
    if no_h264:
        error = tr("This ffmpeg build has no H.264 encoder (libx264).", "这个 ffmpeg 没有 H.264 编码器（libx264）。",
                   "この ffmpeg には H.264 エンコーダー（libx264）がありません。")
    if error and error != "ffmpeg was not found":
        text += "\n" + error
    return text


def open_settings_text() -> str:
    return tr("Choose in Settings", "前往设置选择", "設定で選択")


def waiting_text() -> str:
    return tr("Waiting for ffmpeg…", "等待 ffmpeg…", "ffmpeg を待っています…")


def reading_text() -> str:
    return tr("Reading the video…", "正在读取视频信息…", "動画を読み込んでいます…")


def unreadable_text(error: str) -> str:
    return tr(f"Cannot read this file: {error}", f"无法读取此文件：{error}", f"読み込めません: {error}")


def choose_video_title() -> str:
    return tr("Choose a video", "选择视频", "動画を選択")


def videos_label() -> str:
    return tr("Videos", "视频", "動画")


def all_files_label() -> str:
    return tr("All files", "所有文件", "すべてのファイル")


def failed_title() -> str:
    return tr("Failed", "失败", "失敗")
