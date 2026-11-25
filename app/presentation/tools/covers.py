"""Cover preview (docs/flet_ui_plan.md 7.10): the 16 candidates' texts, cropping and keyboard moves."""
from __future__ import annotations

from pathlib import Path

from ...config import app_config
from ...core.tools.covers import COVER_COUNT, CoverCandidate, format_timecode
from ...core.tools.ffmpeg import MediaInfo
from ...i18n import tr
from .common import format_bytes, format_clock

CROP_KEY = "tools_cover_crop_v1"
CARD_ASPECT = 3 / 2  # Iwara's video cards; the calculator this follows crops to 300×200
COLUMNS = 4
# Arrow keys move the selection through the 4×4 grid.
KEY_STEPS = {"Arrow Left": -1, "Arrow Right": 1, "Arrow Up": -COLUMNS, "Arrow Down": COLUMNS}


def crop_enabled() -> bool:
    return str(app_config.get_ui_value(CROP_KEY, "1")) != "0"


def set_crop(enabled: bool) -> None:
    app_config.set_ui_value(CROP_KEY, "1" if enabled else "0")


def crop_box(width: float, height: float, aspect: float = CARD_ASPECT) -> tuple[float, float, float, float]:
    """``(x, y, w, h)``: the centred ``aspect`` region of a ``width × height`` image."""
    if width <= 0 or height <= 0:
        return 0.0, 0.0, 0.0, 0.0
    if width / height > aspect:
        cropped = height * aspect
        return (width - cropped) / 2, 0.0, cropped, float(height)
    cropped = width / aspect
    return 0.0, (height - cropped) / 2, float(width), cropped


def moved(current: int, step: int, count: int) -> int:
    return max(0, min(count - 1, (current if current >= 0 else 0) + step))


def media_summary(media: MediaInfo) -> str:
    width, height = media.display_size
    frames = media.frame_count or round(media.picture_duration * (media.fps or 30))
    parts = (
        f"{width}×{height}",
        f"{media.fps:.3f}".rstrip("0").rstrip(".") + " fps" if media.fps else "",
        format_clock(media.duration) + f" ({media.duration:.3f}s)",
        tr(f"{frames} frames", f"{frames} 帧", f"{frames} フレーム"),
        format_bytes(media.size),
    )
    return " · ".join(part for part in parts if part)


def extracting_text(done: int) -> str:
    return tr(f"Extracting {done} / {COVER_COUNT}", f"正在截图 {done} / {COVER_COUNT}", f"抽出中 {done} / {COVER_COUNT}")


def finished_text(failed: int) -> str:
    text = tr("All 16 candidates ready. Click one to inspect it.", "16 张候选已全部生成，点选查看。", "16 枚の候補ができました。クリックで確認できます。")
    if failed:
        text += tr(f" {failed} failed.", f" 其中 {failed} 张失败。", f" {failed} 枚失敗しました。")
    return text


def tile_extracting_text() -> str:
    return tr("Extracting…", "截图中…", "抽出中…")


def tile_failed_text(error: str) -> str:
    return tr(f"Failed: {error}", f"失败：{error}", f"失敗: {error}") if error else tr("Failed", "失败", "失敗")


def tile_empty_text() -> str:
    return tr("Load a video", "载入视频后生成", "動画を読み込んでください")


def preview_empty_text() -> str:
    return tr("Pick a candidate to see it here", "点选候选封面，在此查看大图", "候補を選ぶとここに表示されます")


def detail_title(candidate: CoverCandidate) -> str:
    time = format_timecode(candidate.seconds)
    return tr(f"No. {candidate.number} of {COVER_COUNT} · {time}", f"第 {candidate.number} 张（共 {COVER_COUNT} 张）· {time}",
              f"{candidate.number} / {COVER_COUNT} 枚目 · {time}")


def detail_text(candidate: CoverCandidate) -> str:
    return tr(
        f"{candidate.seconds:.6f} s · frame {candidate.frame} (0-based)",
        f"{candidate.seconds:.6f} 秒 · 帧号 {candidate.frame}（第 {candidate.frame + 1} 帧）",
        f"{candidate.seconds:.6f} 秒 · フレーム {candidate.frame}（{candidate.frame + 1} 枚目）",
    )


def default_frame_path(source: Path, candidate: CoverCandidate) -> Path:
    return source.with_name(f"{source.stem}_cover{candidate.number:02d}.png")


def saved_title(ok: bool) -> str:
    return tr("Saved", "已保存", "保存しました") if ok else tr("Failed", "失败", "失敗")
