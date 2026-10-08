"""Download, verify and install a new packaged build from GitHub Releases.

Only the Windows onefile build can replace itself; every other setup falls
back to opening the release page. A download is never installed unless its
SHA-256 matches a digest published with the release.
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from ..logging_setup import get_logger

logger = get_logger(__name__)

_SHA256_RE = re.compile(r"\b([0-9a-fA-F]{64})\b")
_CHUNK = 256 * 1024


@dataclass(frozen=True)
class UpdateAsset:
    name: str
    url: str
    sha256: str  # lowercase hex, "" when the release publishes none
    size: int = 0


def platform_asset_prefix(platform: str | None = None) -> str:
    plat = platform or sys.platform
    if plat.startswith("win"):
        return "IwaraTool-Windows"
    if plat.startswith("linux"):
        return "IwaraTool-Linux"
    return ""


def pick_asset(
    assets: Iterable[dict[str, Any]],
    *,
    platform: str | None = None,
) -> dict[str, Any] | None:
    """Choose the release asset for this platform (the binary, not a checksum)."""
    prefix = platform_asset_prefix(platform)
    if not prefix:
        return None
    for asset in assets or []:
        name = str(asset.get("name", "") or "")
        if name.startswith(prefix) and not name.lower().endswith((".sha256", ".txt")):
            return asset
    return None


def extract_digest(asset: dict[str, Any]) -> str:
    """GitHub exposes ``digest: "sha256:<hex>"`` on release assets."""
    digest = str(asset.get("digest", "") or "")
    if digest.lower().startswith("sha256:"):
        candidate = digest.split(":", 1)[1].strip()
        if _SHA256_RE.fullmatch(candidate):
            return candidate.lower()
    return ""


def parse_checksum_text(text: str, filename: str) -> str:
    """Read a ``sha256sum``-style file (single hash, or ``<hash>  <name>`` lines)."""
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    for line in lines:
        match = _SHA256_RE.search(line)
        if match and filename and filename in line:
            return match.group(1).lower()
    if len(lines) == 1:
        match = _SHA256_RE.search(lines[0])
        if match:
            return match.group(1).lower()
    return ""


def resolve_update_asset(
    assets: list[dict[str, Any]],
    *,
    fetch_text: Callable[[str], str] | None = None,
    platform: str | None = None,
) -> UpdateAsset | None:
    """Pick the platform binary and find its published SHA-256 (if any)."""
    asset = pick_asset(assets, platform=platform)
    if not asset:
        return None
    name = str(asset.get("name", ""))
    sha = extract_digest(asset)
    if not sha and fetch_text is not None:
        for other in assets:
            other_name = str(other.get("name", "") or "")
            if other_name in (f"{name}.sha256", "SHA256SUMS", "SHA256SUMS.txt"):
                try:
                    sha = parse_checksum_text(
                        fetch_text(str(other.get("browser_download_url", ""))), name
                    )
                except Exception:
                    logger.warning("Could not read checksum asset %s", other_name, exc_info=True)
                if sha:
                    break
    return UpdateAsset(
        name=name,
        url=str(asset.get("browser_download_url", "") or ""),
        sha256=sha,
        size=int(asset.get("size", 0) or 0),
    )


class UpdateError(RuntimeError):
    pass


def download_verified(
    asset: UpdateAsset,
    dest_path: str,
    *,
    session: Any,
    proxies: dict[str, str] | None = None,
    progress: Callable[[int, int], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> str:
    """Download ``asset`` to ``dest_path``; raise ``UpdateError`` unless it verifies."""
    if not asset.url:
        raise UpdateError("release asset has no download URL")
    if not asset.sha256:
        raise UpdateError("release publishes no SHA-256 for this file; refusing to install")

    temp_path = f"{dest_path}.part"
    os.makedirs(os.path.dirname(os.path.abspath(dest_path)), exist_ok=True)
    digest = hashlib.sha256()
    response = session.get(asset.url, stream=True, timeout=60, proxies=proxies)
    try:
        response.raise_for_status()
        total = int(response.headers.get("Content-Length", 0) or 0) or asset.size
        done = 0
        with open(temp_path, "wb") as fh:
            for chunk in response.iter_content(chunk_size=_CHUNK):
                if cancelled and cancelled():
                    raise UpdateError("cancelled")
                if not chunk:
                    continue
                fh.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
    except BaseException:
        _remove_quietly(temp_path)
        raise
    finally:
        try:
            response.close()
        except Exception:
            pass

    actual = digest.hexdigest()
    if actual != asset.sha256.lower():
        _remove_quietly(temp_path)
        raise UpdateError(f"SHA-256 mismatch (expected {asset.sha256[:12]}…, got {actual[:12]}…)")
    os.replace(temp_path, dest_path)
    return dest_path


def _remove_quietly(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


# ── Installing ───────────────────────────────────────────────────────────────


def current_executable() -> str:
    """Path of the packaged exe that would be replaced, or ``""`` when running from source."""
    if "__compiled__" not in globals() and not getattr(sys, "frozen", False):
        # Nuitka injects ``__compiled__`` into every compiled module's globals.
        return ""
    path = os.path.abspath(sys.argv[0])
    name = os.path.basename(path).lower()
    if not name.endswith(".exe") or name.startswith("python"):
        return ""
    return path if os.path.isfile(path) else ""


def can_self_update() -> bool:
    return sys.platform == "win32" and bool(current_executable())


def build_swap_script(*, pid: int, new_path: str, target_path: str) -> str:
    """Batch script: wait for ``pid`` to exit, swap the exe, relaunch, self-delete."""
    return "\r\n".join(
        [
            "@echo off",
            "chcp 65001 >NUL",
            "setlocal",
            ":wait",
            f'tasklist /FI "PID eq {pid}" 2>NUL | find "{pid}" >NUL',
            "if not errorlevel 1 (",
            "  timeout /t 1 /nobreak >NUL",
            "  goto wait",
            ")",
            "set /a tries=0",
            ":swap",
            f'move /Y "{new_path}" "{target_path}" >NUL 2>&1',
            "if errorlevel 1 (",
            "  set /a tries+=1",
            "  if %tries% GEQ 20 goto done",
            "  timeout /t 1 /nobreak >NUL",
            "  goto swap",
            ")",
            ":done",
            f'start "" "{target_path}"',
            '(goto) 2>NUL & del "%~f0"',
            "",
        ]
    )


def launch_swap_script(new_path: str, script_dir: str) -> None:
    """Start the swap script detached. The caller must quit the app right after."""
    target = current_executable()
    if not target:
        raise UpdateError("not running from a packaged executable")
    # In a Nuitka onefile build the bootstrapper process holds the exe open.
    pid = int(os.environ.get("NUITKA_ONEFILE_PARENT", "") or os.getpid())
    os.makedirs(script_dir, exist_ok=True)
    script_path = os.path.join(script_dir, "apply_update.bat")
    with open(script_path, "w", encoding="utf-8", newline="") as fh:
        fh.write(build_swap_script(pid=pid, new_path=new_path, target_path=target))
    flags = 0x08000000 | 0x00000008  # CREATE_NO_WINDOW | DETACHED_PROCESS
    subprocess.Popen(
        ["cmd.exe", "/c", script_path],
        creationflags=flags,
        close_fds=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
