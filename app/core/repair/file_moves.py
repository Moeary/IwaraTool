"""Rename and move repaired files safely, also across drives."""

from __future__ import annotations

import errno
import os
import shutil

from ...i18n import tr


class RepairFileMovesMixin:
    """Rename and move repaired files safely, also across drives."""

    def _repair_file_moves(
        self,
        old_path: str,
        new_path: str,
        thumbnail_path: str = "",
    ) -> list[tuple[str, str]]:
        moves = [(old_path, new_path)]
        if self._repair_normalize_path(old_path) == self._repair_normalize_path(new_path):
            return moves
        old_stem = os.path.splitext(old_path)[0]
        new_stem = os.path.splitext(new_path)[0]
        seen_sources = {self._repair_normalize_path(old_path)}
        candidates = [thumbnail_path] if thumbnail_path else []
        candidates.extend(f"{old_stem}{extension}" for extension in (".jpg", ".jpeg", ".png", ".webp", ".nfo"))
        for source in candidates:
            source_key = self._repair_normalize_path(source)
            if not source_key or source_key in seen_sources or not os.path.isfile(source):
                continue
            seen_sources.add(source_key)
            moves.append((source, f"{new_stem}{os.path.splitext(source)[1] or '.jpg'}"))
        return moves

    def _rename_repair_file(
        self,
        old_path: str,
        new_path: str,
        thumbnail_path: str = "",
    ) -> tuple[bool, str, str]:
        if self._repair_normalize_path(old_path) == self._repair_normalize_path(new_path):
            existing_thumb = thumbnail_path if os.path.isfile(thumbnail_path) else ""
            if not existing_thumb:
                existing_thumb = next(
                    (
                        f"{os.path.splitext(old_path)[0]}{extension}"
                        for extension in (".jpg", ".jpeg", ".png", ".webp")
                        if os.path.isfile(f"{os.path.splitext(old_path)[0]}{extension}")
                    ),
                    "",
                )
            return True, existing_thumb, ""
        if os.path.lexists(new_path):
            return False, "", tr(
                f"Target file already exists: {new_path}",
                f"目标文件已存在：{new_path}",
                f"変更先ファイルは既に存在します: {new_path}",
            )

        old_stem = os.path.splitext(old_path)[0]
        moves = self._repair_file_moves(old_path, new_path, thumbnail_path)
        sidecars = moves[1:]

        conflict = self._repair_move_conflict(moves)
        if conflict:
            return False, "", conflict

        renamed: list[tuple[str, str]] = []
        try:
            for source, target in moves:
                if self._repair_normalize_path(source) == self._repair_normalize_path(target):
                    continue
                os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
                # Recheck immediately before each move; the batch plan may be
                # stale if another program created a target in the meantime.
                if os.path.lexists(target):
                    raise FileExistsError(target)
                self._move_repair_file(source, target)
                renamed.append((source, target))
        except Exception as exc:
            for source, target in reversed(renamed):
                try:
                    if os.path.exists(target) and not os.path.exists(source):
                        os.makedirs(os.path.dirname(os.path.abspath(source)), exist_ok=True)
                        self._move_repair_file(target, source)
                except OSError:
                    pass
            return False, "", str(exc)

        new_thumbnail = ""
        for source, target in sidecars:
            if self._repair_normalize_path(source) in {
                self._repair_normalize_path(thumbnail_path),
                self._repair_normalize_path(f"{old_stem}.jpg"),
                self._repair_normalize_path(f"{old_stem}.jpeg"),
                self._repair_normalize_path(f"{old_stem}.png"),
                self._repair_normalize_path(f"{old_stem}.webp"),
            }:
                new_thumbnail = target
                break
        return True, new_thumbnail, ""

    def _repair_move_conflict(self, moves: list[tuple[str, str]]) -> str:
        """Why ``moves`` cannot run (shared or existing target), or ""."""

        target_sources: dict[str, str] = {}
        for source, target in moves:
            source_key = self._repair_normalize_path(source)
            target_key = self._repair_normalize_path(target)
            if target_key in target_sources and target_sources[target_key] != source_key:
                return tr(
                    f"Several files would use the same target: {target}",
                    f"多个文件将使用同一目标：{target}",
                    f"複数のファイルが同じ変更先を使用します: {target}",
                )
            target_sources[target_key] = source_key
            if source_key != target_key and os.path.lexists(target):
                return tr(
                    f"Sidecar target already exists: {target}",
                    f"附属文件目标已存在：{target}",
                    f"関連ファイルの変更先が既に存在します: {target}",
                )
        return ""

    @staticmethod
    def _move_repair_file(source: str, target: str):
        """Move a repair file without shutil.move's overwrite-on-error fallback."""

        try:
            if os.name == "nt":
                # Windows rename rejects an existing destination atomically.
                os.rename(source, target)
            else:
                # A hard link provides the same no-replace guarantee on Unix.
                os.link(source, target)
                try:
                    os.unlink(source)
                except OSError:
                    os.unlink(target)
                    raise
            return
        except OSError as exc:
            if exc.errno != errno.EXDEV and getattr(exc, "winerror", None) != 17:
                raise

        # Organizing can cross drives. Exclusively creating the destination
        # also rejects files created by another process after the preflight.
        created = False
        try:
            with open(source, "rb") as source_stream, open(target, "xb") as target_stream:
                created = True
                shutil.copyfileobj(source_stream, target_stream, length=1024 * 1024)
            shutil.copystat(source, target)
            os.unlink(source)
        except Exception:
            if created:
                os.unlink(target)
            raise
