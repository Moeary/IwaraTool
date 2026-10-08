"""Launch-at-login for Windows via the per-user ``Run`` registry key."""
from __future__ import annotations

import os
import sys

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE_NAME = "IwaraTool"


def is_supported() -> bool:
    return sys.platform == "win32"


def startup_command() -> str:
    """Command line Windows should run at login."""
    from .self_update import current_executable

    packaged = current_executable()
    if packaged:
        return f'"{packaged}"'
    # Running from source: use pythonw so no console window appears.
    interpreter = sys.executable
    pythonw = os.path.join(os.path.dirname(interpreter), "pythonw.exe")
    if os.path.isfile(pythonw):
        interpreter = pythonw
    script = os.path.abspath(sys.argv[0])
    return f'"{interpreter}" "{script}"'


def is_enabled() -> bool:
    if not is_supported():
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.QueryValueEx(key, _VALUE_NAME)
            return True
    except OSError:
        return False


def set_enabled(enabled: bool) -> None:
    if not is_supported():
        raise OSError("launch at login is only supported on Windows")
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
        if enabled:
            winreg.SetValueEx(key, _VALUE_NAME, 0, winreg.REG_SZ, startup_command())
        else:
            try:
                winreg.DeleteValue(key, _VALUE_NAME)
            except FileNotFoundError:
                pass
