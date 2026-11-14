"""Preview the 16 thumbnails Iwara offers as covers for an uploaded video.

After an upload Iwara lets the author pick one of 16 screenshots. They appear
to be taken at the middle of 16 equal slices of the video, i.e. at
``duration × (2i + 1) / 32`` for ``i = 0 … 15`` (a community finding, not
documented by Iwara). Computing them before uploading shows whether a good
frame will be on offer. ``duration`` is the video stream's, so music that runs
on after the picture ends does not push covers past the last frame.
"""

from __future__ import annotations

import subprocess
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .ffmpeg import FfmpegError, FfmpegInfo, subprocess_flags

COVER_COUNT = 16
PREVIEW_BOX = 960  # preview frames fit in a 960×960 box


@dataclass(frozen=True)
class CoverCandidate:
    index: int  # 0-based
    seconds: float
    frame: int  # 0-based frame number at the given fps

    @property
    def number(self) -> int:
        return self.index + 1


def cover_times(duration: float, count: int = COVER_COUNT) -> list[float]:
    if duration <= 0:
        return []
    return [duration * (2 * i + 1) / (2 * count) for i in range(count)]


def cover_candidates(duration: float, fps: float) -> list[CoverCandidate]:
    rate = fps if fps > 0 else 30.0
    # Duration includes the last frame's display interval. Seeking inside that
    # interval can return EOF, so short clips must stop at its start instead.
    last_frame_time = max(0.0, duration - 1 / rate)
    return [
        CoverCandidate(index, seconds, max(0, round(seconds * rate)))
        for index, seconds in enumerate(min(t, last_frame_time) for t in cover_times(duration))
    ]


def format_timecode(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    secs, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{milliseconds:03d}"


def frame_command(ffmpeg: FfmpegInfo, source: Path, seconds: float, output: str = "-", *, preview: bool = True) -> list[str]:
    """Decode the frame at ``seconds`` exactly (input seeking is frame-accurate)."""

    argv = [
        str(ffmpeg.path), "-hide_banner", "-nostdin", "-loglevel", "error",
        "-ss", f"{max(seconds, 0):.6f}", "-i", str(source), "-frames:v", "1", "-an", "-sn",
    ]
    if preview:
        argv += ["-vf", f"scale=w={PREVIEW_BOX}:h={PREVIEW_BOX}:force_original_aspect_ratio=decrease"]
    if output == "-":
        return [*argv, "-f", "image2pipe", "-c:v", "png", "-"]
    return [*argv, "-y", "-update", "1", output]


def extract_frame(ffmpeg: FfmpegInfo, source: Path, seconds: float, *, preview: bool = True) -> bytes:
    """PNG bytes of one frame."""

    try:
        result = subprocess.run(
            frame_command(ffmpeg, source, seconds, preview=preview),
            capture_output=True,
            timeout=120,
            **subprocess_flags(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise FfmpegError(str(exc)) from exc
    if result.returncode != 0 or not result.stdout:
        message = result.stderr.decode("utf-8", "replace").strip()
        raise FfmpegError(message or "No frame at this time")
    return result.stdout


def save_frame(ffmpeg: FfmpegInfo, source: Path, seconds: float, target: Path) -> None:
    """Write the full-resolution frame to ``target`` (format from its extension)."""

    try:
        result = subprocess.run(
            frame_command(ffmpeg, source, seconds, str(target), preview=False),
            capture_output=True,
            timeout=120,
            **subprocess_flags(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise FfmpegError(str(exc)) from exc
    if result.returncode != 0 or not target.is_file():
        raise FfmpegError(result.stderr.decode("utf-8", "replace").strip() or "Could not save the frame")


def extract_covers(
    ffmpeg: FfmpegInfo,
    source: Path,
    candidates: list[CoverCandidate],
    on_frame: Callable[[CoverCandidate, bytes | None, str], None],
    *,
    should_stop: Callable[[], bool] = lambda: False,
    workers: int = 4,
) -> None:
    """Extract every candidate, calling ``on_frame(candidate, png, error)`` as each finishes."""

    def work(candidate: CoverCandidate):
        if should_stop():
            return
        try:
            data = extract_frame(ffmpeg, source, candidate.seconds)
        except FfmpegError as exc:
            on_frame(candidate, None, str(exc))
            return
        on_frame(candidate, data, "")

    with ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="cover") as pool:
        list(pool.map(work, candidates))
