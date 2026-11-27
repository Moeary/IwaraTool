"""Upload compressor: options, the verdict on a file, progress texts.

``CompressForm`` is what the option controls hold (saved as JSON under
``OPTIONS_KEY``); ``verdict`` turns a probed file and its plan into the
result card; colours are role names (``success``, ``warning``, ``danger``).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from ...config import app_config
from ...core.tools.compress import SIZE_PRESETS_MB, UPSCALERS, CompressOptions, CompressPlan, default_output_path
from ...core.tools.ffmpeg import FfmpegInfo, MediaInfo
from ...i18n import tr
from .common import format_bytes, format_clock

OPTIONS_KEY = "tools_compress_options_v1"
CUSTOM_MB_RANGE = (10, 20000)
MARGIN_RANGE = (0, 20)

_DEFAULTS = CompressOptions()


@dataclass(frozen=True)
class CompressForm:
    limit: str = "standard"  # standard | premium | custom
    custom_mb: int = 300
    margin_pct: int = round(_DEFAULTS.margin * 100)
    encoder: str = _DEFAULTS.encoder
    x264_preset: str = _DEFAULTS.x264_preset
    audio_kbps: int = _DEFAULTS.audio_kbps
    max_fps: float = _DEFAULTS.max_fps
    upscaler: str = _DEFAULTS.upscaler
    auto_downscale: bool = _DEFAULTS.auto_downscale
    force_encode: bool = False  # not saved: a one-off choice

    @property
    def custom(self) -> bool:
        return self.limit == "custom"

    @property
    def x264(self) -> bool:
        return (self.encoder or "libx264") == "libx264"

    def options(self) -> CompressOptions:
        return CompressOptions(
            limit_mb=float(SIZE_PRESETS_MB.get(self.limit, self.custom_mb)),
            margin=self.margin_pct / 100,
            encoder=str(self.encoder or "libx264"),
            x264_preset=str(self.x264_preset or "medium"),
            audio_kbps=int(self.audio_kbps or 160),
            auto_downscale=self.auto_downscale,
            upscaler=str(self.upscaler or "lanczos"),
            max_fps=float(self.max_fps or 0.0),
            force_encode=self.force_encode,
        )


def saved_form() -> CompressForm:
    try:
        saved = json.loads(str(app_config.get_ui_value(OPTIONS_KEY, "") or "{}"))
    except ValueError:
        saved = {}
    if not isinstance(saved, dict):
        saved = {}
    base = CompressForm()
    try:
        return CompressForm(
            limit=str(saved.get("limit", base.limit)),
            custom_mb=int(saved.get("custom_mb", base.custom_mb) or base.custom_mb),
            margin_pct=int(saved.get("margin_pct", base.margin_pct)),
            encoder=str(saved.get("encoder", base.encoder)),
            x264_preset=str(saved.get("x264_preset", base.x264_preset)),
            audio_kbps=int(saved.get("audio_kbps", base.audio_kbps)),
            max_fps=float(saved.get("max_fps", base.max_fps)),
            upscaler=str(saved.get("upscaler", base.upscaler)),
            auto_downscale=bool(saved.get("auto_downscale", base.auto_downscale)),
        )
    except (TypeError, ValueError):
        return base


def save_form(form: CompressForm) -> None:
    data = asdict(form)
    data.pop("force_encode", None)
    app_config.set_ui_value(OPTIONS_KEY, json.dumps(data))


def replace_encoder(form: CompressForm, encoder: str) -> CompressForm:
    return replace(form, encoder=encoder)


def available_encoders(info: FfmpegInfo | None) -> list[str]:
    if info is None:
        return []
    return (["libx264"] if info.has_libx264 else []) + list(info.hardware_encoders)


def ffmpeg_ready(info: FfmpegInfo | None) -> bool:
    return bool(available_encoders(info))


def pick_encoder(form: CompressForm, info: FfmpegInfo | None) -> CompressForm:
    """The saved encoder when this ffmpeg has it, else its first one."""
    names = available_encoders(info)
    if names and form.encoder not in names:
        return replace(form, encoder=names[0])
    return form


# ── choices ──────────────────────────────────────────────────────────────────


def limit_choices() -> list[tuple[str, str]]:
    return [("standard", tr("Standard · 300 MB", "普通用户 · 300MB", "一般 · 300 MB")),
            ("premium", tr("Premium · 600 MB", "会员 · 600MB", "プレミアム · 600 MB")),
            ("custom", tr("Custom", "自定义", "カスタム"))]


def fps_choices() -> list[tuple[float, str]]:
    return [(60.0, tr("Keep (cap at 60)", "保持原帧率（最高 60）", "そのまま（上限 60）")), (30.0, "30 fps"),
            (0.0, tr("Keep exactly", "完全保持", "完全に維持"))]


def upscaler_choices() -> list[tuple[str, str]]:
    return [(name, {"lanczos": "Lanczos", "spline": "Spline", "bicubic": "Bicubic"}.get(name, name)) for name in UPSCALERS]


def encoder_label(name: str) -> str:
    return {
        "libx264": tr("x264 two-pass (best quality for the size)", "x264 两遍编码（同体积画质最好）", "x264 2 パス（サイズ当たり最高画質）"),
        "h264_videotoolbox": tr("VideoToolbox (Apple hardware, fast)", "VideoToolbox（Apple 硬件，快）", "VideoToolbox（Apple ハードウェア・高速）"),
        "h264_nvenc": tr("NVENC (NVIDIA hardware, fast)", "NVENC（NVIDIA 硬件，快）", "NVENC（NVIDIA ハードウェア・高速）"),
        "h264_qsv": tr("Quick Sync (Intel hardware, fast)", "Quick Sync（Intel 硬件，快）", "Quick Sync（Intel ハードウェア・高速）"),
        "h264_amf": tr("AMF (AMD hardware, fast)", "AMF（AMD 硬件，快）", "AMF（AMD ハードウェア・高速）"),
    }.get(name, name)


# ── the file and its plan ────────────────────────────────────────────────────


def media_summary(media: MediaInfo) -> str:
    width, height = media.display_size
    audio = f" / {media.audio_codec}" if media.audio_codec else tr(" / no audio", " / 无音轨", " / 音声なし")
    rate = (media.bit_rate or (media.size * 8 / media.duration if media.duration else 0)) / 1000
    parts = [
        f"{width}×{height}",
        f"{media.fps:.3g} fps" if media.fps else "",
        format_clock(media.duration),
        format_bytes(media.size),
        f"{media.video_codec}{audio}",
        f"{rate:.0f} kbps",
    ]
    return " · ".join(part for part in parts if part)


def plan_note_text(note: str) -> str:
    return {
        "remux": tr(
            "The video stream already qualifies; only the container / audio is converted (fast, no quality loss).",
            "视频流已符合要求，只转换封装或音频（很快，画质无损）。",
            "映像はそのまま使えます。コンテナ／音声だけ変換します（高速・無劣化）。",
        ),
        "upscaled": tr("Below 720p: scaled up to 720p as Iwara requires.", "低于 720p：按 Iwara 要求放大到 720p。",
                       "720p 未満のため 720p に拡大します。"),
        "downscaled": tr(
            "The size budget is too small for the source resolution, so it is scaled down to keep the picture clean.",
            "体积预算撑不起原分辨率，已降低分辨率以保证画面干净。",
            "容量に対して解像度が高すぎるため、画質を保つよう縮小します。",
        ),
        "fps_capped": tr("The frame rate is capped.", "已限制帧率。", "フレームレートを制限します。"),
        "low_quality": tr(
            "The bitrate is very low for this length; expect visible blocking. Trimming the video or a lower frame rate helps.",
            "按这个时长码率很低，画面会明显糊块；剪短视频或降低帧率会好些。",
            "長さに対してビットレートが低く、ブロックノイズが目立ちます。短くするかフレームレートを下げてください。",
        ),
        "too_long": tr(
            "Too long to fit under the limit even at the lowest usable bitrate. Split or trim the video, or use the premium limit.",
            "即使用最低可用码率也装不进上限。请分段或剪短，或改用会员上限。",
            "最低ビットレートでも上限に収まりません。分割・短縮するか、プレミアムの上限を使ってください。",
        ),
        "retried": tr(
            "The first encode came out over the limit and was redone at a lower bitrate.",
            "第一次编码超出上限，已降低码率重新编码。",
            "最初のエンコードが上限を超えたため、低いビットレートでやり直しました。",
        ),
    }.get(note, note)


def needs_text(media: MediaInfo, options: CompressOptions) -> str:
    reasons = []
    if media.size > options.limit_bytes:
        reasons.append(tr(f"over {options.limit_mb:g} MB", f"超过 {options.limit_mb:g}MB", f"{options.limit_mb:g} MB 超過"))
    if media.short_side < 720:
        reasons.append(tr("below 720p", "低于 720p", "720p 未満"))
    if media.video_codec != "h264":
        reasons.append(tr(f"video is {media.video_codec}", f"视频编码为 {media.video_codec}", f"映像が {media.video_codec}"))
    if media.has_audio and media.audio_codec != "aac":
        reasons.append(tr(f"audio is {media.audio_codec}", f"音频编码为 {media.audio_codec}", f"音声が {media.audio_codec}"))
    if not media.is_mp4:
        reasons.append(tr("not an MP4 file", "不是 MP4 封装", "MP4 ではない"))
    if not reasons:
        return tr("Re-encoding as requested.", "按要求重新编码。", "指定どおり再エンコードします。")
    return tr("Needs processing: ", "需要处理：", "処理が必要: ") + tr(", ", "，", "、").join(reasons)


@dataclass(frozen=True)
class Verdict:
    role: str  # success | warning | danger
    title: str
    detail: str
    notes: tuple[str, ...]


def verdict(media: MediaInfo, plan: CompressPlan, options: CompressOptions) -> Verdict:
    """The result card for a probed file: can it go up as is, what will be done, what to expect."""
    notes = tuple(plan_note_text(note) for note in plan.notes if note != "remux" or plan.mode == "remux")
    if plan.mode == "ready":
        return Verdict("success", tr(
            "✓ Ready to upload as it is: H.264 + AAC MP4, at least 720p and under the limit.",
            "✓ 已满足上传要求，可直接上传：H.264 + AAC 的 MP4，不低于 720p，且未超过上限。",
            "✓ このままアップロードできます（H.264 + AAC の MP4、720p 以上、上限以内）。",
        ), tr(
            "Tick “Re-encode even if the file already qualifies” to compress it anyway.",
            "如仍要压缩，请勾选“即使已符合要求也重新编码”。",
            "それでも圧縮する場合は「条件を満たしていても再エンコードする」をオンにします。",
        ), notes)
    if plan.mode == "impossible":
        return Verdict("danger", tr("✗ Cannot fit under the limit", "✗ 无法压到上限以内", "✗ 上限内に収まりません"), tr(
            f"Only {plan.video_kbps} kbps would be left for the picture.",
            f"画面只剩 {plan.video_kbps} kbps 可用。",
            f"映像に使えるのは {plan.video_kbps} kbps だけです。",
        ), notes)
    size = format_bytes(plan.estimated_bytes)
    if plan.mode == "remux":
        audio = tr("copy", "直接复制", "コピー") if plan.copy_audio or not plan.has_audio else f"AAC {plan.audio_kbps} kbps"
        detail = tr(
            f"Output: {plan.width}×{plan.height}, video copied, audio {audio}, about {size}",
            f"输出：{plan.width}×{plan.height}，视频直接复制，音频 {audio}，约 {size}",
            f"出力: {plan.width}×{plan.height}、映像コピー、音声 {audio}、約 {size}",
        )
    else:
        audio = f" + AAC {plan.audio_kbps} kbps" if plan.has_audio else ""
        limit = options.limit_mb
        detail = tr(
            f"Output: {plan.width}×{plan.height} @ {plan.fps:.3g} fps, H.264 {plan.video_kbps} kbps{audio}, about {size} (limit {limit:g} MB)",
            f"输出：{plan.width}×{plan.height} @ {plan.fps:.3g} fps，H.264 {plan.video_kbps} kbps{audio}，约 {size}（上限 {limit:g}MB）",
            f"出力: {plan.width}×{plan.height} @ {plan.fps:.3g} fps、H.264 {plan.video_kbps} kbps{audio}、約 {size}（上限 {limit:g} MB）",
        )
    return Verdict("warning", needs_text(media, options), detail, notes)


# ── output and running ───────────────────────────────────────────────────────


def output_for(text: str, source: Path) -> tuple[Path | None, str]:
    """The MP4 to write (``.mp4`` added, a blank box gets the default); ``(None, why)`` if it is the source."""
    text = str(text or "").strip()
    output = Path(text) if text else default_output_path(source)
    if output.suffix.lower() != ".mp4":
        output = output.with_suffix(".mp4")
    try:
        same = output.resolve() == source.resolve()
    except OSError:
        same = False
    if same:
        return None, tr("Choose a different output file.", "请换一个输出文件名，不能覆盖源文件。", "元ファイルとは別の出力先を選んでください。")
    return output, ""


def stage_text(stage: str) -> str:
    return {
        "pass1": tr("Pass 1 of 2 (analysis)", "第 1 遍（分析）", "1 パス目（解析）"),
        "pass2": tr("Pass 2 of 2 (encoding)", "第 2 遍（编码）", "2 パス目（エンコード）"),
        "encode": tr("Encoding", "编码中", "エンコード中"),
        "remux": tr("Converting the container", "转换封装中", "コンテナ変換中"),
        "done": tr("Finishing", "收尾中", "仕上げ中"),
    }.get(stage, stage)


def progress_text(stage: str, overall: float, elapsed: float) -> str:
    eta = ""
    if 0.02 < overall < 1:
        left = format_clock(elapsed / overall - elapsed)
        eta = tr(f", about {left} left", f"，约剩 {left}", f"、残り約 {left}")
    return f"{stage_text(stage)} · {overall * 100:.0f}%{eta}"


def starting_text() -> str:
    return tr("Starting ffmpeg…", "正在启动 ffmpeg…", "ffmpeg を起動しています…")


def stopping_text() -> str:
    return tr("Stopping…", "正在停止…", "停止中…")


def cancelled_text() -> str:
    return tr("Cancelled", "已取消", "キャンセルしました")


def done_text(size: int, elapsed: float) -> str:
    return tr(f"Done: {format_bytes(size)} in {format_clock(elapsed)}", f"完成：{format_bytes(size)}，用时 {format_clock(elapsed)}",
              f"完了: {format_bytes(size)}（{format_clock(elapsed)}）")


def done_title() -> str:
    return tr("Compressed", "压缩完成", "圧縮完了")
