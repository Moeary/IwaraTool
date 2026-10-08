"""Backup and restore of the portable ``data/`` folder.

A backup never contains credentials (account, login token, aria2 secret) and
skips caches. Restoring is staged: the archive is validated and unpacked into
``data/restore_pending`` and applied by :func:`apply_pending_restore` at the
next start, before any database or settings file is opened.

This module must stay importable before ``app.config`` (no Qt, no config).
"""
from __future__ import annotations

import os
import re
import shutil
import sqlite3
import tempfile
import time
import zipfile
from datetime import datetime

MANIFEST_NAME = "backup_manifest.txt"
PENDING_DIR_NAME = "restore_pending"
SAFETY_DIR_NAME = "backup_before_restore"

# Only these top-level files travel in a backup (caches, logs and updates do not).
BACKUP_FILES = (
    "config.ini",
    "history.db",
    "tasks.json",
    "rules.json",
    "subscriptions.sources.json",
    "x_version_salts.json",
)
_SQLITE_FILES = frozenset({"history.db"})
SENSITIVE_KEYS = (
    "username",
    "password",
    "auth_token",
    "auth_token_saved_at",
    "aria2_rpc_token",
)
_SENSITIVE_LINE = re.compile(
    r"^(?:%s)\s*=" % "|".join(re.escape(key) for key in SENSITIVE_KEYS)
)
_MAX_MEMBER_BYTES = 2 * 1024 ** 3


class BackupError(RuntimeError):
    pass


def default_backup_name(now: datetime | None = None) -> str:
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    return f"IwaraTool_backup_{stamp}.zip"


# ── config.ini credential handling ───────────────────────────────────────────


def strip_sensitive(text: str) -> str:
    """Drop credential keys from QSettings INI text."""
    kept = [line for line in text.splitlines() if not _SENSITIVE_LINE.match(line.strip())]
    return "\n".join(kept) + ("\n" if kept else "")


def sensitive_lines(text: str) -> list[str]:
    return [
        line.strip()
        for line in text.splitlines()
        if _SENSITIVE_LINE.match(line.strip())
    ]


def merge_credentials(restored_text: str, current_text: str) -> str:
    """Restored settings plus the credentials that are currently logged in."""
    keep = sensitive_lines(current_text)
    body = strip_sensitive(restored_text).splitlines()
    if not keep:
        return "\n".join(body) + "\n"
    for index, line in enumerate(body):
        if line.strip().lower() == "[general]":
            body[index + 1:index + 1] = keep
            break
    else:
        body = ["[General]", *keep, *body]
    return "\n".join(body) + "\n"


# ── Creating ─────────────────────────────────────────────────────────────────


def create_backup(data_dir: str, dest_zip: str) -> list[str]:
    """Write a credential-free backup archive; returns the stored file names."""
    stored: list[str] = []
    with tempfile.TemporaryDirectory(prefix="iwara_backup_") as scratch:
        with zipfile.ZipFile(dest_zip, "w", zipfile.ZIP_DEFLATED) as archive:
            for name in BACKUP_FILES:
                source = os.path.join(data_dir, name)
                if not os.path.isfile(source):
                    continue
                if name in _SQLITE_FILES:
                    snapshot = os.path.join(scratch, name)
                    _snapshot_sqlite(source, snapshot)
                    archive.write(snapshot, name)
                elif name == "config.ini":
                    with open(source, "r", encoding="utf-8", errors="replace") as fh:
                        archive.writestr(name, strip_sensitive(fh.read()))
                else:
                    archive.write(source, name)
                stored.append(name)
            archive.writestr(
                MANIFEST_NAME,
                f"IwaraTool backup\ncreated={datetime.now().isoformat(timespec='seconds')}\n"
                f"files={','.join(stored)}\ncredentials=excluded\n",
            )
    return stored


def _snapshot_sqlite(source: str, target: str) -> None:
    """Consistent copy even while the app writes (handles WAL)."""
    src = sqlite3.connect(source, timeout=30)
    try:
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


# ── Restoring ────────────────────────────────────────────────────────────────


def stage_restore(zip_path: str, data_dir: str) -> list[str]:
    """Validate ``zip_path`` and unpack it for the next start; returns file names."""
    try:
        archive = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise BackupError(f"not a valid backup archive: {exc}") from exc
    with archive:
        if MANIFEST_NAME not in archive.namelist():
            raise BackupError("not an IwaraTool backup (manifest missing)")
        members = []
        for info in archive.infolist():
            name = info.filename
            if name == MANIFEST_NAME:
                continue
            if name not in BACKUP_FILES:
                raise BackupError(f"unexpected entry in backup: {name!r}")
            if info.file_size > _MAX_MEMBER_BYTES:
                raise BackupError(f"entry too large: {name!r}")
            members.append(info)
        if not members:
            raise BackupError("backup contains no data files")

        pending = os.path.join(data_dir, PENDING_DIR_NAME)
        shutil.rmtree(pending, ignore_errors=True)
        os.makedirs(pending, exist_ok=True)
        staged: list[str] = []
        for info in members:
            with archive.open(info) as src, open(os.path.join(pending, info.filename), "wb") as dst:
                shutil.copyfileobj(src, dst)
            staged.append(info.filename)
    return staged


def has_pending_restore(data_dir: str) -> bool:
    pending = os.path.join(data_dir, PENDING_DIR_NAME)
    return os.path.isdir(pending) and bool(os.listdir(pending))


def apply_pending_restore(data_dir: str) -> list[str]:
    """Swap staged files into ``data_dir``; the old ones are kept as a safety copy."""
    pending = os.path.join(data_dir, PENDING_DIR_NAME)
    if not has_pending_restore(data_dir):
        return []
    safety = os.path.join(
        data_dir, SAFETY_DIR_NAME, time.strftime("%Y%m%d_%H%M%S")
    )
    os.makedirs(safety, exist_ok=True)
    applied: list[str] = []
    for name in sorted(os.listdir(pending)):
        if name not in BACKUP_FILES:
            continue
        staged = os.path.join(pending, name)
        target = os.path.join(data_dir, name)
        current_text = ""
        if os.path.isfile(target):
            if name == "config.ini":
                with open(target, "r", encoding="utf-8", errors="replace") as fh:
                    current_text = fh.read()
            shutil.copy2(target, os.path.join(safety, name))
        if name in _SQLITE_FILES:
            for suffix in ("-wal", "-shm"):
                leftover = target + suffix
                if os.path.exists(leftover):
                    shutil.copy2(leftover, os.path.join(safety, name + suffix))
                    os.remove(leftover)
        if name == "config.ini":
            with open(staged, "r", encoding="utf-8", errors="replace") as fh:
                merged = merge_credentials(fh.read(), current_text)
            with open(target, "w", encoding="utf-8", newline="") as fh:
                fh.write(merged)
        else:
            os.replace(staged, target)
        applied.append(name)
    shutil.rmtree(pending, ignore_errors=True)
    return applied
