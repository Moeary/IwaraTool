"""Preview the target names a repair would give the scanned files."""

from __future__ import annotations

import os
from typing import Any

from ...i18n import tr
from .matching import repair_target_path
from ..task_metadata import _dict_or_empty


class RepairPreviewMixin:
    """Preview the target names a repair would give the scanned files."""

    def preview_repair_files(
        self,
        items: list[dict[str, Any]],
        *,
        options: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Plan video/sidecar targets without writing files or resolving metadata."""

        normalized = self._repair_options(options)
        result = [dict(item) for item in items]
        target_claims: dict[str, list[tuple[int, str, str]]] = {}
        source_claims: dict[str, set[int]] = {}
        conflicts: dict[int, list[str]] = {}

        def add_conflict(index: int, message: str):
            messages = conflicts.setdefault(index, [])
            if message not in messages:
                messages.append(message)

        for index, item in enumerate(result):
            if item.get("status") != "ready":
                continue
            item["target_conflict"] = ""
            path = str(item.get("path", "") or "")
            try:
                item.update(self._repair_target_fields(item, normalized))
                target_path = str(item["target_path"])
                cached = _dict_or_empty(item.get("cached_meta"))
                claims = self._repair_item_claims(path, target_path, cached, normalized)

                for source, target in claims:
                    source_key = self._repair_normalize_path(source)
                    target_key = self._repair_normalize_path(target)
                    target_claims.setdefault(target_key, []).append((index, source_key, target))
                    if os.path.isfile(source):
                        source_claims.setdefault(source_key, set()).add(index)
                    if source_key != target_key and os.path.lexists(target):
                        add_conflict(index, tr(
                            f"Target already exists: {target}",
                            f"目标已存在：{target}",
                            f"変更先は既に存在します: {target}",
                        ))
            except Exception as exc:
                add_conflict(index, tr(
                    f"Cannot plan repair: {exc}",
                    f"无法预览修复目标：{exc}",
                    f"修復先を準備できません: {exc}",
                ))

        self._add_shared_claim_conflicts(target_claims, source_claims, add_conflict)
        for index, messages in conflicts.items():
            result[index]["target_conflict"] = "\n".join(messages)
        return result

    def _repair_item_claims(self, path: str, target_path: str, cached: dict, normalized: dict[str, Any]) -> list[tuple[str, str]]:
        """``(source, target)`` pairs one item would write: the video, its moved and new sidecars."""

        moves = self._repair_file_moves(
            path, target_path, str(cached.get("thumbnail_path", "") or ""),
        ) if normalized["rename"] else [(path, path)]
        claims = list(moves)
        claimed_targets = {self._repair_normalize_path(target) for _, target in claims}
        # Two formats of the same basename can have different video
        # destinations but still collide on a newly generated sidecar.
        for option, extension in (("download_thumbnail", ".jpg"), ("collect_nfo", ".nfo")):
            if not normalized[option]:
                continue
            target = f"{os.path.splitext(target_path)[0]}{extension}"
            target_key = self._repair_normalize_path(target)
            if target_key not in claimed_targets:
                source = f"{os.path.splitext(path)[0]}{extension}"
                claims.append((source, target))
                claimed_targets.add(target_key)
        return claims

    @staticmethod
    def _add_shared_claim_conflicts(target_claims, source_claims, add_conflict):
        """Flag items that would write the same target or move the same source."""

        for claims in target_claims.values():
            owners = {(index, source) for index, source, _ in claims}
            if len(owners) <= 1:
                continue
            target = claims[0][2]
            message = tr(
                f"Several files would use the same target: {target}",
                f"多个文件将使用同一目标：{target}",
                f"複数のファイルが同じ変更先を使用します: {target}",
            )
            for index, _, _ in claims:
                add_conflict(index, message)
        for source, owners in source_claims.items():
            if len(owners) <= 1:
                continue
            message = tr(
                f"The same source belongs to several repair items: {source}",
                f"同一源文件属于多个修复项目：{source}",
                f"同じ元ファイルが複数の修復項目に含まれています: {source}",
            )
            for index in owners:
                add_conflict(index, message)

    @staticmethod
    def _repair_target_fields(item: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
        path = str(item.get("path", "") or "")
        moving = bool(options.get("move_to_output", False))
        output_root = str(options.get("output_root", "") or "")
        if moving and not output_root and item.get("move_to_output"):
            # A scan with a blank organize output uses its selected source
            # root, even when the videos lie several directories below it.
            output_root = str(item.get("output_root", "") or "")
        if options.get("rename", True):
            cached = _dict_or_empty(item.get("cached_meta"))
            metadata = _dict_or_empty(item.get("metadata"))
            target_name, target_path = repair_target_path(
                options.get("filename_template", ""), cached or metadata, path,
                output_root=output_root,
                move_to_output=moving,
            )
        else:
            target_name, target_path = os.path.basename(path), os.path.abspath(path)
        return {
            "target_name": target_name,
            "target_relative_path": target_name,
            "target_path": target_path,
            "output_root": output_root if moving and options.get("rename", True) and output_root else os.path.dirname(os.path.abspath(path)),
            "move_to_output": moving,
        }

    @staticmethod
    def _repair_normalize_path(path: Any) -> str:
        raw = str(path or "").strip()
        if not raw:
            return ""
        return os.path.normcase(os.path.abspath(os.path.expanduser(raw)))
