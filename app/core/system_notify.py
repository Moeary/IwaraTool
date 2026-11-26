"""System notifications without Qt (the Flet interface; the classic one uses its tray icon).

Each platform's own tool shows the notification: ``osascript`` on macOS, a
PowerShell toast on Windows, ``notify-send`` on Linux.  ``notify`` blocks for
up to a few seconds, so callers run it in a worker thread; it never raises.
"""
from __future__ import annotations

import shutil
import subprocess
import sys

from ..logging_setup import get_logger

logger = get_logger(__name__)

TIMEOUT = 10
# Windows only shows toasts for a registered app id; PowerShell's own is always registered.
WINDOWS_APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"


def _applescript_text(text: str) -> str:
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _xml_text(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&apos;"))


def command(title: str, message: str, platform: str | None = None) -> list[str] | None:
    """The command that shows one notification on ``platform`` (default: this one), or None when it has no tool."""
    platform = platform or sys.platform
    title, message = str(title or "IwaraTool")[:200], str(message or "")[:500]
    if platform == "darwin":
        return ["osascript", "-e",
                f"display notification {_applescript_text(message)} with title {_applescript_text(title)}"]
    if platform == "win32":
        # Double-quoted attributes and escaped text: nothing can end PowerShell's single-quoted string.
        xml = (f'<toast><visual><binding template="ToastGeneric"><text>{_xml_text(title)}</text>'
               f"<text>{_xml_text(message)}</text></binding></visual></toast>")
        script = (
            "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null;"
            "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null;"
            "$x = New-Object Windows.Data.Xml.Dom.XmlDocument;"
            f"$x.LoadXml('{xml}');"
            "$t = [Windows.UI.Notifications.ToastNotification]::new($x);"
            f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{WINDOWS_APP_ID}').Show($t)"
        )
        return ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", script]
    if shutil.which("notify-send"):
        return ["notify-send", "--app-name=IwaraTool", title, message]
    return None


def notify(title: str, message: str) -> bool:
    """Show a system notification; False when the platform has no way or it failed."""
    args = command(title, message)
    if args is None:
        return False
    extra = {}
    if sys.platform == "win32":
        extra["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(args, capture_output=True, timeout=TIMEOUT, **extra)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("System notification failed: %s", exc)
        return False
    if result.returncode != 0:
        logger.debug("System notification failed: %s", result.stderr[-300:])
    return result.returncode == 0
