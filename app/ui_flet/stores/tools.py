"""Tools page state (docs/flet_ui_plan.md 7.10): the ffmpeg lookup, the compressor and the cover preview.

ffmpeg, ffprobe and every frame grab run in worker threads; the compressor
is cancelled through ``Compressor.cancel`` (it ends ffmpeg), the cover grabs
through a generation number.  Results come back to the loop in the page
context (frame grabs report from their own pool threads).  Lives on the
shell, so a running compression survives leaving the page.
"""
from __future__ import annotations

import asyncio
import contextvars
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

import flet as ft

from ...config import app_config
from ...core.tools import covers
from ...core.tools.compress import Cancelled, Compressor, CompressPlan, plan_compression
from ...core.tools.ffmpeg import FfmpegError, FfmpegInfo, MediaInfo, locate, probe
from ...logging_setup import get_logger
from ...presentation.tools import common as tc
from ...presentation.tools import compress as cr
from ...presentation.tools import covers as cv
from ..services.notify import notice

logger = get_logger(__name__)


@ft.observable
@dataclass
class ToolsState:
    tool: str = "compress"
    # ffmpeg
    ffmpeg_checked: bool = False
    ffmpeg_busy: bool = False
    ffmpeg_error: str = ""
    # compressor
    form: cr.CompressForm = field(default_factory=cr.CompressForm)
    source: str = ""
    media_text: str = ""
    media_error: bool = False
    probing: bool = False
    output: str = ""
    running: bool = False
    progress: float = 0.0
    status: str = ""
    last_output: str = ""
    failure_log: str = ""
    retried: bool = False
    # covers
    cover_source: str = ""
    cover_text: str = ""
    cover_error: bool = False
    cover_status: str = ""
    cover_revision: int = 0
    selected: int = -1
    crop: bool = True
    saving: bool = False


async def _off_loop(fn, *args):
    return await asyncio.to_thread(fn, *args)


class ToolsStore:
    def __init__(self, shell):
        self.shell = shell
        self.state = ToolsState()
        self.ffmpeg: FfmpegInfo | None = None
        self.media: MediaInfo | None = None
        self.plan: CompressPlan | None = None
        self.output_touched = False
        self._probe_generation = 0
        self._compressor: Compressor | None = None
        self._started_at = 0.0
        # covers
        self.cover_media: MediaInfo | None = None
        self.candidates: list[covers.CoverCandidate] = []
        self.frames: dict[int, bytes] = {}
        self.frame_errors: dict[int, str] = {}
        self._cover_generation = 0
        self.grid_columns = cv.COLUMNS
        self.loaded = False

    # ── setup and ffmpeg ─────────────────────────────────────────────────────

    def load(self) -> None:
        if self.loaded:
            return
        self.loaded = True
        saved = str(app_config.get_ui_value(tc.TOOL_KEY, "compress") or "compress")
        self.state.tool = saved if saved in {t.key for t in tc.tools()} else "compress"
        self.state.form = cr.saved_form()
        self.state.crop = cv.crop_enabled()
        self.shell.page.run_task(self.refresh_ffmpeg)

    def choose_tool(self, key: str) -> None:
        self.state.tool = key
        app_config.set_ui_value(tc.TOOL_KEY, key)

    @property
    def compress_ready(self) -> bool:
        return cr.ffmpeg_ready(self.ffmpeg)

    async def refresh_ffmpeg(self) -> None:
        """Look ffmpeg up (again, after Settings changed the path); ``ffmpeg -encoders`` takes a moment."""
        if self.state.ffmpeg_busy:
            return
        self.state.ffmpeg_busy = True
        try:
            self.ffmpeg, self.state.ffmpeg_error = await _off_loop(locate, str(app_config.ffmpeg_path or "")), ""
        except FfmpegError as exc:
            self.ffmpeg, self.state.ffmpeg_error = None, str(exc)
        except Exception as exc:  # a broken binary must not take the page down
            logger.exception("Locating ffmpeg failed")
            self.ffmpeg, self.state.ffmpeg_error = None, str(exc)
        finally:
            self.state.ffmpeg_busy = False
            self.state.ffmpeg_checked = True
        self.state.form = cr.pick_encoder(self.state.form, self.ffmpeg)
        if self.ffmpeg is not None:
            if self.state.source and self.media is None:
                await self.load_source(self.state.source, force=True)
            if self.state.cover_source and self.cover_media is None:
                await self.load_cover_source(self.state.cover_source, force=True)

    def _threadsafe(self, fn):
        loop = self.shell.loop
        context = contextvars.copy_context()

        def call(*args) -> None:
            loop.call_soon_threadsafe(fn, *args, context=context.copy())

        return call

    # ── compressor ───────────────────────────────────────────────────────────

    def set_form(self, **changes) -> None:
        self.state.form = replace(self.state.form, **changes)
        cr.save_form(self.state.form)
        self._replan()

    def set_output(self, text: str) -> None:
        self.output_touched = True
        self.state.output = text

    def _replan(self) -> None:
        self.plan = plan_compression(self.media, self.state.form.options()) if self.media is not None else None
        self.state.retried = False

    def verdict(self) -> cr.Verdict | None:
        if self.media is None or self.plan is None:
            return None
        return cr.verdict(self.media, self.plan, self.state.form.options())

    @property
    def can_start(self) -> bool:
        return (not self.state.running and not self.state.probing and self.compress_ready
                and self.plan is not None and self.plan.runnable)

    async def load_source(self, path: str, *, force: bool = False) -> None:
        path = tc.clean_path(path)
        state = self.state
        if state.running or (not force and path == state.source and (self.media is not None or state.probing)):
            return
        state.source = path
        self.media = self.plan = None
        self._probe_generation += 1
        generation = self._probe_generation
        state.last_output = state.failure_log = state.status = ""
        state.progress = 0.0
        state.media_error = False
        if not path:
            state.media_text = ""
            return
        if not self.output_touched:
            state.output = str(cr.default_output_path(Path(path)))
        if self.ffmpeg is None:
            state.media_text = tc.waiting_text()
            return
        state.media_text = tc.reading_text()
        state.probing = True
        try:
            media = await _off_loop(probe, self.ffmpeg, path)
        except Exception as exc:
            if generation == self._probe_generation:
                state.media_text, state.media_error = tc.unreadable_text(str(exc)), True
            return
        finally:
            if generation == self._probe_generation:
                state.probing = False
        if generation != self._probe_generation:
            return
        self.media = media
        state.media_text = cr.media_summary(media)
        self._replan()

    async def start(self) -> None:
        if not self.can_start or self.media is None or self.plan is None or self.ffmpeg is None:
            return
        state = self.state
        output, error = cr.output_for(state.output, self.media.path)
        if output is None:
            notice(self.shell.page, "error", tc.failed_title(), error)
            return
        state.output = str(output)
        compressor = Compressor(self.ffmpeg, self.media, self.plan, state.form.options(), output)
        self._compressor = compressor
        state.running, state.progress, state.failure_log, state.last_output = True, 0.0, "", ""
        state.status = cr.starting_text()
        self._started_at = time.monotonic()
        on_progress = self._threadsafe(self._on_progress)
        try:
            result = await _off_loop(lambda: compressor.run(lambda stage, _part, overall: on_progress(stage, overall)))
        except Cancelled:
            state.progress, state.status = 0.0, cr.cancelled_text()
            return
        except Exception as exc:
            logger.exception("Compression failed")
            state.status = tc.failed_title()
            state.failure_log = compressor.stderr_tail or str(exc)
            notice(self.shell.page, "error", tc.failed_title(), str(exc).splitlines()[-1] if str(exc) else "")
            return
        finally:
            self._compressor = None
            state.running = False
        state.progress = 1.0
        state.last_output = str(result.output)
        state.status = cr.done_text(result.size, result.elapsed)
        state.retried = "retried" in result.plan.notes
        self.output_touched = False
        notice(self.shell.page, "success", cr.done_title(), str(result.output))

    def _on_progress(self, stage: str, overall: float) -> None:
        if self.state.running:
            self.state.progress = overall
            self.state.status = cr.progress_text(stage, overall, time.monotonic() - self._started_at)

    def cancel(self) -> None:
        if self._compressor is not None:
            self._compressor.cancel()
            self.state.status = cr.stopping_text()

    # ── covers ───────────────────────────────────────────────────────────────

    async def load_cover_source(self, path: str, *, force: bool = False) -> None:
        path = tc.clean_path(path)
        state = self.state
        if not force and path == state.cover_source and self.cover_media is not None:
            return
        self._cover_generation += 1
        generation = self._cover_generation
        state.cover_source = path
        self.cover_media = None
        self.candidates, self.frames, self.frame_errors = [], {}, {}
        state.selected = -1
        state.cover_error = False
        state.cover_status = ""
        state.cover_revision += 1
        if not path:
            state.cover_text = ""
            return
        if self.ffmpeg is None:
            state.cover_text = tc.waiting_text()
            return
        state.cover_text = tc.reading_text()
        try:
            media = await _off_loop(probe, self.ffmpeg, path)
        except Exception as exc:
            if generation == self._cover_generation:
                state.cover_text, state.cover_error = tc.unreadable_text(str(exc)), True
            return
        if generation != self._cover_generation:
            return
        self.cover_media = media
        state.cover_text = cv.media_summary(media)
        self.candidates = covers.cover_candidates(media.picture_duration, media.fps)
        state.cover_status = cv.extracting_text(0)
        state.cover_revision += 1
        on_frame = self._threadsafe(lambda candidate, data, error: self._on_frame(generation, candidate, data, error))
        ffmpeg = self.ffmpeg
        await _off_loop(lambda: covers.extract_covers(
            ffmpeg, media.path, self.candidates, on_frame, should_stop=lambda: generation != self._cover_generation))

    def _on_frame(self, generation: int, candidate: covers.CoverCandidate, data: bytes | None, error: str) -> None:
        if generation != self._cover_generation:
            return
        if data:
            self.frames[candidate.index] = data
        else:
            self.frame_errors[candidate.index] = error
        done = len(self.frames) + len(self.frame_errors)
        if done >= len(self.candidates):
            self.state.cover_status = cv.finished_text(len(self.frame_errors))
            if self.state.selected < 0:
                self.state.selected = 0
        else:
            self.state.cover_status = cv.extracting_text(done)
        self.state.cover_revision += 1

    def select(self, index: int) -> None:
        if 0 <= index < len(self.candidates):
            self.state.selected = index

    def handle_key(self, key: str) -> bool:
        """Arrow keys move the cover selection; True when the key was used."""
        step = cv.key_step(key, self.grid_columns)
        if step is None or self.state.tool != "covers":
            return False
        self.move(step)
        return True

    def move(self, step: int) -> None:
        if self.candidates:
            self.select(cv.moved(self.state.selected, step, len(self.candidates)))

    def set_crop(self, enabled: bool) -> None:
        cv.set_crop(enabled)
        self.state.crop = enabled

    @property
    def selected(self) -> covers.CoverCandidate | None:
        index = self.state.selected
        return self.candidates[index] if 0 <= index < len(self.candidates) else None

    async def save_frame(self, target: str) -> None:
        candidate, media, ffmpeg = self.selected, self.cover_media, self.ffmpeg
        if not target or candidate is None or media is None or ffmpeg is None:
            return
        self.state.saving = True
        try:
            await _off_loop(covers.save_frame, ffmpeg, media.path, candidate.seconds, Path(target))
        except Exception as exc:
            notice(self.shell.page, "error", cv.saved_title(False), str(exc))
            return
        finally:
            self.state.saving = False
        notice(self.shell.page, "success", cv.saved_title(True), target)

    def close(self) -> None:
        self._cover_generation += 1
        if self._compressor is not None:
            self._compressor.cancel()

    @property
    def busy(self) -> bool:
        return self.state.running
