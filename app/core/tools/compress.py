"""Fit a video under Iwara's upload limit as H.264 + AAC in MP4.

Iwara accepts up to 300 MB (600 MB for premium members). The plan works out
what a file needs:

* ``ready``  — already H.264/AAC MP4, at least 720p and under the limit;
* ``remux``  — the video stream is fine, only the container or audio changes;
* ``encode`` — re-encode at the bitrate that fills the size budget.

Encoding defaults to two-pass libx264, which hits a target size closely and
looks best for its size. Hardware encoders are faster but drift from the
target, so an output that comes out too big is re-encoded at a lower bitrate.
Videos whose short side is under 720 are scaled up to 720; when the budget is
too small for the source resolution the video is scaled down (never below
720) rather than turned into blocks.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

from .ffmpeg import FfmpegError, FfmpegInfo, MediaInfo, subprocess_flags

MB = 1_000_000  # Iwara does not say MB or MiB; decimal is the safer reading.
SIZE_PRESETS_MB = {"standard": 300, "premium": 600}
MIN_SHORT_SIDE = 720
DOWNSCALE_STEPS = (2160, 1440, 1080, 720)
MIN_VIDEO_KBPS = 150

# Bits per pixel per frame below which H.264 visibly falls apart, and above
# which a lower resolution would not look better.
LOW_QUALITY_BPP = 0.03
DOWNSCALE_BPP = 0.05

X264_PRESETS = ("veryfast", "faster", "fast", "medium", "slow", "slower")
AUDIO_BITRATES = (128, 160, 192, 256, 320)

# Scaler flags for upscaling sub-720p sources (ffmpeg's built-in scaler).
UPSCALERS = {
    "lanczos": "lanczos",
    "spline": "spline",
    "bicubic": "bicubic",
}


@dataclass
class CompressOptions:
    limit_mb: float = SIZE_PRESETS_MB["standard"]
    margin: float = 0.03
    encoder: str = "libx264"
    x264_preset: str = "medium"
    audio_kbps: int = 160
    auto_downscale: bool = True
    upscaler: str = "lanczos"
    max_fps: float = 60.0  # 0 keeps the source rate
    force_encode: bool = False

    @property
    def limit_bytes(self) -> int:
        return int(self.limit_mb * MB)

    @property
    def budget_bytes(self) -> int:
        return int(self.limit_bytes * (1 - max(0.0, min(self.margin, 0.5))))


@dataclass
class CompressPlan:
    mode: str  # ready / remux / encode / impossible
    width: int
    height: int
    fps: float
    video_kbps: int = 0
    audio_kbps: int = 0
    copy_audio: bool = False
    has_audio: bool = True
    scaled: bool = False
    upscaled: bool = False
    downscaled: bool = False
    fps_capped: bool = False
    estimated_bytes: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def runnable(self) -> bool:
        return self.mode in ("remux", "encode")

    @property
    def bpp(self) -> float:
        pixels = self.width * self.height * (self.fps or 30.0)
        return self.video_kbps * 1000 / pixels if pixels else 0.0


def _even(value: float) -> int:
    return max(2, int(round(value / 2)) * 2)


def scaled_size(width: int, height: int, short_side: int) -> tuple[int, int]:
    """Scale so the shorter side becomes ``short_side``, keeping the aspect."""

    if width <= 0 or height <= 0:
        return width, height
    if width >= height:
        return _even(width * short_side / height), _even(short_side)
    return _even(short_side), _even(height * short_side / width)


def _bitrate_ceiling_kbps(short_side: int) -> int:
    """More than this buys nothing visible; keeps short clips from ballooning."""

    if short_side <= 720:
        return 6000
    if short_side <= 1080:
        return 10000
    if short_side <= 1440:
        return 16000
    return 30000


def _video_ok(media: MediaInfo) -> bool:
    return (
        media.video_codec == "h264"
        and media.pix_fmt in ("yuv420p", "yuvj420p", "")
        and media.short_side >= MIN_SHORT_SIDE
    )


def _audio_ok(media: MediaInfo) -> bool:
    return not media.has_audio or media.audio_codec == "aac"


def plan_compression(media: MediaInfo, options: CompressOptions) -> CompressPlan:
    """Decide what has to happen to ``media`` to be uploadable."""

    width, height = media.display_size
    fps = media.fps or 30.0
    plan = CompressPlan(mode="encode", width=width, height=height, fps=fps, has_audio=media.has_audio)
    duration = max(media.duration, 0.001)

    if not options.force_encode and _video_ok(media):
        if _audio_ok(media) and media.is_mp4 and media.size <= options.limit_bytes:
            plan.mode = "ready"
            plan.estimated_bytes = media.size
            plan.copy_audio = True
            return plan
        audio_bytes = 0
        plan.copy_audio = _audio_ok(media)
        if media.has_audio:
            plan.audio_kbps = 0 if plan.copy_audio else options.audio_kbps
            audio_rate = media.audio_bit_rate if plan.copy_audio else options.audio_kbps * 1000
            audio_bytes = int(audio_rate * duration / 8)
        if media.video_bit_rate:
            video_bytes = int(media.video_bit_rate * duration / 8)
        else:
            video_bytes = max(0, media.size - int((media.audio_bit_rate or 0) * duration / 8))
        estimate = int((video_bytes + audio_bytes) * 1.005)
        if estimate <= options.limit_bytes:
            plan.mode = "remux"
            plan.estimated_bytes = estimate
            plan.notes.append("remux")
            return plan

    plan.copy_audio = False
    plan.audio_kbps = options.audio_kbps if media.has_audio else 0
    if options.max_fps and fps > options.max_fps + 0.01:
        plan.fps = float(options.max_fps)
        plan.fps_capped = True
        plan.notes.append("fps_capped")

    total_kbps = options.budget_bytes * 8 / duration / 1000
    video_kbps = int(total_kbps - plan.audio_kbps)
    if video_kbps < MIN_VIDEO_KBPS:
        plan.mode = "impossible"
        plan.video_kbps = max(video_kbps, 0)
        plan.notes.append("too_long")
        return plan

    short = min(width, height)
    target_short = max(short, MIN_SHORT_SIDE)
    if short < MIN_SHORT_SIDE:
        plan.upscaled = True
        plan.notes.append("upscaled")
    elif options.auto_downscale:
        while target_short > MIN_SHORT_SIDE:
            out_w, out_h = scaled_size(width, height, target_short)
            if video_kbps * 1000 / (out_w * out_h * plan.fps) >= DOWNSCALE_BPP:
                break
            target_short = next((step for step in DOWNSCALE_STEPS if step < target_short), MIN_SHORT_SIDE)
        if target_short < short:
            plan.downscaled = True
            plan.notes.append("downscaled")
    if target_short != short:
        plan.width, plan.height = scaled_size(width, height, target_short)
        plan.scaled = True
    else:
        plan.width, plan.height = _even(width), _even(height)
        plan.scaled = (plan.width, plan.height) != (width, height)

    ceiling = _bitrate_ceiling_kbps(target_short)
    if not plan.scaled and media.video_codec == "h264" and media.video_bit_rate:
        # Re-encoding H.264 at a higher rate than it already has gains nothing.
        ceiling = min(ceiling, max(MIN_VIDEO_KBPS, int(media.video_bit_rate / 1000)))
    plan.video_kbps = min(video_kbps, ceiling)
    plan.estimated_bytes = int((plan.video_kbps + plan.audio_kbps) * 1000 * duration / 8 * 1.01)
    if plan.bpp < LOW_QUALITY_BPP:
        plan.notes.append("low_quality")
    return plan


# ── Commands ─────────────────────────────────────────────────────────────────


def _video_filters(plan: CompressPlan, options: CompressOptions) -> list[str]:
    filters = []
    if plan.scaled:
        flags = UPSCALERS.get(options.upscaler, "lanczos") if plan.upscaled else "lanczos"
        filters.append(f"scale={plan.width}:{plan.height}:flags={flags}")
        filters.append("setsar=1")
    if plan.fps_capped:
        filters.append(f"fps={plan.fps:g}")
    return filters


def _video_codec_args(plan: CompressPlan, options: CompressOptions, video_kbps: int) -> list[str]:
    rate = f"{video_kbps}k"
    encoder = options.encoder
    if encoder == "libx264":
        return [
            "-c:v", "libx264", "-preset", options.x264_preset, "-b:v", rate,
            "-pix_fmt", "yuv420p", "-profile:v", "high",
        ]
    peak = ["-maxrate", f"{int(video_kbps * 1.5)}k", "-bufsize", f"{video_kbps * 2}k"]
    if encoder == "h264_videotoolbox":
        return ["-c:v", encoder, "-b:v", rate, "-pix_fmt", "yuv420p", "-profile:v", "high", *peak]
    if encoder == "h264_nvenc":
        return ["-c:v", encoder, "-preset", "p5", "-rc", "vbr", "-b:v", rate, *peak, "-pix_fmt", "yuv420p", "-profile:v", "high"]
    if encoder == "h264_qsv":
        return ["-c:v", encoder, "-preset", "medium", "-b:v", rate, *peak, "-pix_fmt", "nv12", "-profile:v", "high"]
    if encoder == "h264_amf":
        return ["-c:v", encoder, "-rc", "vbr_peak", "-b:v", rate, *peak, "-pix_fmt", "yuv420p", "-profile:v", "high"]
    raise FfmpegError(f"Unsupported encoder: {encoder}")


def _audio_args(plan: CompressPlan, media: MediaInfo) -> list[str]:
    if not media.has_audio:
        return ["-an"]
    if plan.copy_audio:
        return ["-c:a", "copy"]
    channels = max(1, min(media.audio_channels or 2, 2))
    return ["-c:a", "aac", "-b:a", f"{plan.audio_kbps}k", "-ac", str(channels)]


def _input_args(ffmpeg: FfmpegInfo, source: Path) -> list[str]:
    return [
        str(ffmpeg.path), "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
        "-progress", "pipe:1", "-nostats", "-i", str(source),
        "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn", "-map_chapters", "-1",
    ]


def build_commands(
    ffmpeg: FfmpegInfo,
    media: MediaInfo,
    plan: CompressPlan,
    options: CompressOptions,
    output: Path,
    passlog: Path,
    *,
    video_kbps: int | None = None,
    second_pass_only: bool = False,
) -> list[tuple[str, list[str]]]:
    """``(stage, argv)`` pairs to run in order; ``output`` is written as MP4."""

    tail = ["-movflags", "+faststart", "-f", "mp4", str(output)]
    if plan.mode == "remux":
        return [("remux", [*_input_args(ffmpeg, media.path), "-c:v", "copy", *_audio_args(plan, media), *tail])]
    if plan.mode != "encode":
        return []
    rate = int(video_kbps or plan.video_kbps)
    filters = _video_filters(plan, options)
    vf = ["-vf", ",".join(filters)] if filters else []
    codec = _video_codec_args(plan, options, rate)
    if options.encoder != "libx264":
        return [("encode", [*_input_args(ffmpeg, media.path), *vf, *codec, *_audio_args(plan, media), *tail])]
    log = ["-passlogfile", str(passlog)]
    first = [*_input_args(ffmpeg, media.path), *vf, *codec, "-pass", "1", *log, "-an", "-f", "null", os.devnull]
    second = [*_input_args(ffmpeg, media.path), *vf, *codec, "-pass", "2", *log, *_audio_args(plan, media), *tail]
    if second_pass_only:
        return [("pass2", second)]
    return [("pass1", first), ("pass2", second)]


# ── Running ──────────────────────────────────────────────────────────────────


class Cancelled(Exception):
    pass


class FfmpegProcess:
    """One ffmpeg run reporting progress from ``-progress pipe:1``."""

    def __init__(self, argv: list[str], duration: float):
        self.argv = argv
        self.duration = max(duration, 0.001)
        self._proc: subprocess.Popen | None = None
        self._cancelled = threading.Event()
        self._stderr: deque[str] = deque(maxlen=60)

    def cancel(self):
        self._cancelled.set()
        proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass

    @property
    def stderr_tail(self) -> str:
        return "\n".join(self._stderr)

    def _drain_stderr(self, stream):
        for line in stream:
            line = line.rstrip()
            if line:
                self._stderr.append(line)

    def run(self, on_progress: Callable[[float], None] | None = None) -> None:
        if self._cancelled.is_set():
            raise Cancelled()
        try:
            self._proc = subprocess.Popen(
                self.argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                **subprocess_flags(),
            )
        except OSError as exc:
            raise FfmpegError(str(exc)) from exc
        proc = self._proc
        assert proc.stdout is not None and proc.stderr is not None
        drain = threading.Thread(target=self._drain_stderr, args=(proc.stderr,), daemon=True)
        try:
            drain.start()
            # Cancellation may arrive while Popen is still creating the child.
            if self._cancelled.is_set():
                self.cancel()
            for line in proc.stdout:
                key, _, value = line.strip().partition("=")
                if key in ("out_time_us", "out_time_ms") and on_progress is not None:
                    try:
                        seconds = int(value) / 1_000_000
                    except ValueError:
                        continue
                    on_progress(max(0.0, min(seconds / self.duration, 1.0)))
                elif key == "progress" and value == "end" and on_progress is not None:
                    on_progress(1.0)
            code = proc.wait()
        finally:
            # Also reap the child if reading stdout or the progress callback
            # raises; closing its pipes alone can leave ffmpeg running.
            try:
                if proc.poll() is None:
                    proc.kill()
                proc.wait()
            finally:
                if drain.ident is not None:
                    drain.join()
                proc.stdout.close()
                proc.stderr.close()
        if self._cancelled.is_set():
            raise Cancelled()
        if code != 0:
            raise FfmpegError(self.stderr_tail or f"ffmpeg exited with code {code}")


@dataclass
class CompressResult:
    output: Path
    size: int
    plan: CompressPlan
    attempts: int
    elapsed: float


ProgressCallback = Callable[[str, float, float], None]  # stage, stage fraction, overall fraction


class Compressor:
    """Run a plan, re-encoding at a lower rate if the result is over the limit."""

    MAX_RETRIES = 2

    def __init__(self, ffmpeg: FfmpegInfo, media: MediaInfo, plan: CompressPlan, options: CompressOptions, output: Path):
        self.ffmpeg = ffmpeg
        self.media = media
        self.plan = plan
        self.options = options
        self.output = Path(output)
        self._current: FfmpegProcess | None = None
        self._cancelled = threading.Event()

    def cancel(self):
        self._cancelled.set()
        current = self._current
        if current is not None:
            current.cancel()

    @property
    def stderr_tail(self) -> str:
        return self._current.stderr_tail if self._current is not None else ""

    def _run_stages(self, stages, on_progress: ProgressCallback | None, base: float, span: float):
        count = len(stages)
        for index, (stage, argv) in enumerate(stages):
            if self._cancelled.is_set():
                raise Cancelled()
            process = FfmpegProcess(argv, self.media.duration)
            self._current = process

            def report(fraction, stage=stage, index=index):
                if on_progress is not None:
                    on_progress(stage, fraction, base + span * (index + fraction) / count)

            process.run(report)

    def run(self, on_progress: ProgressCallback | None = None) -> CompressResult:
        if not self.plan.runnable:
            raise FfmpegError(f"Nothing to run for plan {self.plan.mode}")
        if self.output.resolve() == self.media.path.resolve():
            raise FfmpegError("The output would overwrite the source file")
        started = time.monotonic()
        partial = self.output.with_name(self.output.name + ".part")
        workdir = Path(tempfile.mkdtemp(prefix="iwaratool-ffmpeg-"))
        passlog = workdir / "pass"
        plan = self.plan
        attempts = 0
        try:
            self.output.parent.mkdir(parents=True, exist_ok=True)
            stages = build_commands(self.ffmpeg, self.media, plan, self.options, partial, passlog)
            # A retry only repeats the last pass, so give it a smaller share.
            self._run_stages(stages, on_progress, 0.0, 0.9 if plan.mode == "encode" else 1.0)
            attempts = 1
            size = partial.stat().st_size
            if plan.mode == "remux" and size > self.options.limit_bytes:
                # The stream bitrates the remux estimate relied on were off.
                plan = plan_compression(self.media, replace(self.options, force_encode=True))
                if not plan.runnable:
                    raise FfmpegError(f"Output is {size / MB:.1f} MB, over the {self.options.limit_mb:g} MB limit")
                stages = build_commands(self.ffmpeg, self.media, plan, self.options, partial, passlog)
                self._run_stages(stages, on_progress, 0.0, 0.9)
                attempts += 1
                size = partial.stat().st_size
            while plan.mode == "encode" and size > self.options.limit_bytes and attempts <= self.MAX_RETRIES:
                rate = int(plan.video_kbps * self.options.budget_bytes / size * 0.97)
                if rate < MIN_VIDEO_KBPS:
                    break
                plan = replace(plan, video_kbps=rate, notes=[*plan.notes, "retried"])
                stages = build_commands(
                    self.ffmpeg, self.media, plan, self.options, partial, passlog,
                    second_pass_only=self.options.encoder == "libx264",
                )
                self._run_stages(stages, on_progress, 0.9, 0.1)
                attempts += 1
                size = partial.stat().st_size
            if size > self.options.limit_bytes:
                raise FfmpegError(f"Output is still {size / MB:.1f} MB, over the {self.options.limit_mb:g} MB limit")
            os.replace(partial, self.output)
            if on_progress is not None:
                on_progress("done", 1.0, 1.0)
            return CompressResult(self.output, size, plan, attempts, time.monotonic() - started)
        except BaseException:
            try:
                partial.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        finally:
            shutil.rmtree(workdir, ignore_errors=True)


def default_output_path(source: Path) -> Path:
    """``name_iwara.mp4`` beside the source, numbered if that exists."""

    base = source.with_name(f"{source.stem}_iwara.mp4")
    candidate, number = base, 2
    while candidate.exists():
        candidate = source.with_name(f"{source.stem}_iwara_{number}.mp4")
        number += 1
    return candidate
