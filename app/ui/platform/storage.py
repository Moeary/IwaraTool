"""Where a phone keeps downloads and the image cache.

The core's default download folder sits next to the program, which on
Android is the unpacked app (replaced on update and invisible to the user).
Until the user picks a folder, downloads go to the shared
``Download/IwaraTool`` folder, and covers, thumbnails and avatars to its
hidden ``.cache`` (with ``.nomedia`` so galleries skip them).  Settings, the
databases and logs stay in the app's private data folder.

On the first start the app asks for "All files access" (Android 11+) or the
storage permission (older versions).  Without it Android still lets the app
create files in ``Download``, but not read back files that an earlier
install made, so the cache then stays in the data folder (its fixed file
names would collide); downloads still go to ``Download/IwaraTool``.  When
even that cannot be written, downloads fall back to the app-specific
``Android/data/<package>/files/Downloads`` folder.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import flet as ft

from ...config import app_config
from ...i18n import tr
from ...logging_setup import get_logger
from ...paths import app_data_dir, default_download_dir

logger = get_logger(__name__)

PUBLIC_FOLDER = "IwaraTool"  # under the shared Download folder
CACHE_FOLDER = ".cache"
DOWNLOAD_FOLDER = "Downloads"  # under the app-specific folder, the last resort
ASKED_KEY = "android_storage_permission_asked"
DEFAULT_SHARED_ROOT = Path("/storage/emulated/0")


AUTO_KEY = "android_auto_download_dir"  # the folder this module chose last (not one the user picked)


def is_beta_default(path: str) -> bool:
    """``Android/data/<package>/files/Downloads``, where the v1.3 beta put downloads by itself."""
    folded = str(path or "").replace("\\", "/").rstrip("/").casefold()
    return "/android/data/" in folded and folded.endswith("/files/" + DOWNLOAD_FOLDER.casefold())


def _same(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def using_default_folder() -> bool:
    """The download folder is still one the app chose, so it may move."""
    current = str(app_config.download_dir or "").strip()
    if not current or is_beta_default(current) or _same(current, str(default_download_dir())):
        return True
    chosen = str(app_config.get_ui_value(AUTO_KEY, "") or "")
    return bool(chosen) and _same(current, chosen)


def shared_root(external: str | None) -> Path:
    """``/storage/emulated/0`` from the app-specific ``…/Android/data/<package>/files``."""
    if external:
        parts = Path(external).parts
        if "Android" in parts:
            return Path(*parts[: parts.index("Android")])
    return DEFAULT_SHARED_ROOT


def public_download_dir(external: str | None) -> Path:
    return shared_root(external) / "Download" / PUBLIC_FOLDER


def phone_download_dir(external: str | None) -> Path:
    """The last resort: the app's external files directory, else its private data folder."""
    base = Path(external) if external else Path(app_data_dir())
    return base / DOWNLOAD_FOLDER


def writable(folder: Path) -> bool:
    probe = folder / f".write-test-{os.getpid()}"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe.write_bytes(b"")
        probe.unlink()
        return True
    except OSError:
        return False


def prepare_cache_dir(folder: Path) -> bool:
    try:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / ".nomedia").touch(exist_ok=True)
        return True
    except OSError:
        logger.warning("Cannot use %s for the image cache", folder, exc_info=True)
        return False


def export_logs(target: Path) -> Path:
    """Copy the log files to ``target/logs`` (phones cannot open the private data folder)."""
    import shutil

    from ...logging_setup import log_dir

    destination = target / "logs"
    destination.mkdir(parents=True, exist_ok=True)
    source = Path(log_dir())
    if source.is_dir():
        for item in source.iterdir():
            if item.is_file():
                shutil.copyfile(item, destination / item.name)
    return destination


async def _granted(handler, permission) -> bool:
    from flet_permission_handler import PermissionStatus

    try:
        return await handler.get_status(permission) == PermissionStatus.GRANTED
    except Exception:
        logger.debug("Permission status of %s unknown", permission, exc_info=True)
        return False


async def _request(handler, permission) -> bool:
    from flet_permission_handler import PermissionStatus

    try:
        return await handler.request(permission) == PermissionStatus.GRANTED
    except Exception:
        logger.warning("Requesting %s failed", permission, exc_info=True)
        return False


async def android_sdk(page: ft.Page) -> int:
    """The Android API level (30 = Android 11); 30 when unknown, the stricter case."""
    try:
        info = await page.get_device_info()
        return int(info.version.sdk)
    except Exception:
        logger.debug("Android version unknown", exc_info=True)
        return 30


async def storage_access(page: ft.Page) -> bool:
    """Full access to shared storage; asks once (the first start), later only checks."""
    try:
        from flet_permission_handler import Permission, PermissionHandler
    except ImportError:
        return False
    handler = PermissionHandler()
    # Android 11+: All files access; up to 10: the storage permission.
    # (On 11–12 the storage permission only reads media, which cannot reuse an earlier install's cache.)
    wanted = (Permission.MANAGE_EXTERNAL_STORAGE,) if await android_sdk(page) >= 30 else (
        Permission.MANAGE_EXTERNAL_STORAGE, Permission.STORAGE)
    for permission in wanted:
        if await _granted(handler, permission):
            return True
    if app_config.get_ui_flag(ASKED_KEY, False):
        return False
    app_config.set_ui_value(ASKED_KEY, True)
    from ..services.dialogs import confirm

    if not await confirm(
        page,
        tr("Storage access", "存储权限", "ストレージへのアクセス"),
        tr("IwaraTool saves downloads and its picture cache to Download/IwaraTool. "
           "Allow access to files on the next screen so they keep working after the app is reinstalled.",
           "IwaraTool 会把下载的视频和图片缓存保存到 Download/IwaraTool。"
           "请在接下来的页面中允许访问文件，这样重装应用后也能继续使用它们。",
           "IwaraTool はダウンロードと画像キャッシュを Download/IwaraTool に保存します。"
           "次の画面でファイルへのアクセスを許可すると、再インストール後も引き続き使えます。"),
        confirm_label=tr("Continue", "继续", "続ける"),
        cancel_label=tr("Not now", "暂不", "後で"),
        informative=tr("You can change this later in the system settings.", "之后可以在系统设置中更改。",
                       "後からシステム設定で変更できます。"),
    ):
        return False
    for permission in wanted:
        if await _request(handler, permission):
            return True
    return False


async def prepare_phone_storage(page: ft.Page) -> None:
    """Android: downloads and the image cache to ``Download/IwaraTool`` (see the module notes)."""
    if page.platform != ft.PagePlatform.ANDROID:
        return
    external = None
    try:
        external = await ft.StoragePaths().get_external_storage_directory()
    except Exception:
        logger.warning("No external storage directory", exc_info=True)
    full_access = await storage_access(page)
    public = public_download_dir(external)
    if await asyncio.to_thread(writable, public):
        target = public
        cache = public / CACHE_FOLDER
        if full_access and await asyncio.to_thread(prepare_cache_dir, cache):
            app_config.image_cache_dir = str(cache)
    else:
        logger.warning("Cannot write to %s; downloads stay in the app's folder", public)
        target = phone_download_dir(external)
    if not using_default_folder():
        return
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.exception("Cannot create %s", target)
        return
    app_config.set_ui_value(AUTO_KEY, str(target))
    if str(app_config.download_dir or "") == str(target):
        return
    app_config.download_dir = str(target)
    from ...core.download.manager import download_manager

    await asyncio.to_thread(download_manager.apply_config)
