"""Launch at login (Windows, macOS, Linux).

- Windows: ``HKCU\\...\\Run`` value ``IwaraTool``, the value v1.2 and earlier
  wrote, so switching the option also replaces or removes an older version's entry;
- macOS: ``~/Library/LaunchAgents/io.github.moeary.iwaratool.plist``
  (launchd loads it at the next login);
- Linux: ``~/.config/autostart/iwaratool.desktop``.

The packaged app starts itself; a run from source starts the interpreter on
``main.py``.
"""
from __future__ import annotations

import os
import plistlib
import sys
from pathlib import Path, PurePosixPath

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "IwaraTool"
AGENT_LABEL = "io.github.moeary.iwaratool"
DESKTOP_NAME = "iwaratool.desktop"
SCRIPT = Path(__file__).resolve().parents[2] / "main.py"


def is_supported(platform: str = sys.platform) -> bool:
    return platform in ("win32", "darwin") or platform.startswith("linux")


def _packaged() -> bool:
    return bool(os.environ.get("FLET_PLATFORM"))


def process_executable() -> str:
    """The running program's own file (in a packaged app: the app's binary, not a Python)."""
    try:
        import ctypes

        if sys.platform == "win32":
            buffer = ctypes.create_unicode_buffer(32768)
            ctypes.windll.kernel32.GetModuleFileNameW(None, buffer, len(buffer))
            return buffer.value
        if sys.platform == "darwin":
            buffer = ctypes.create_string_buffer(4096)
            size = ctypes.c_uint32(len(buffer))
            if ctypes.CDLL(None)._NSGetExecutablePath(buffer, ctypes.byref(size)) == 0:
                return os.path.realpath(buffer.value.decode())
        return os.path.realpath("/proc/self/exe")
    except Exception:
        return sys.executable


def app_bundle(executable: str) -> str:
    """``/Applications/IwaraTool.app`` for ``.../IwaraTool.app/Contents/MacOS/IwaraTool``; "" outside a bundle."""
    for parent in PurePosixPath(executable).parents:  # a macOS path, whatever this machine is
        if parent.suffix == ".app":
            return str(parent)
    return ""


def launch_arguments(packaged: bool | None = None, executable: str | None = None,
                     platform: str = sys.platform) -> list[str]:
    """What the system should run at login."""
    packaged = _packaged() if packaged is None else packaged
    executable = executable or (process_executable() if packaged else sys.executable)
    if packaged:
        bundle = app_bundle(executable) if platform == "darwin" else ""
        return ["/usr/bin/open", "-a", bundle] if bundle else [executable]
    interpreter = executable
    if platform == "win32":
        pythonw = os.path.join(os.path.dirname(interpreter), "pythonw.exe")
        interpreter = pythonw if os.path.isfile(pythonw) else interpreter
    return [interpreter, str(SCRIPT)]


def _quote_windows(args: list[str]) -> str:
    return " ".join(f'"{a}"' for a in args)


def agent_path(home: Path | None = None) -> Path:
    return (home or Path.home()) / "Library" / "LaunchAgents" / f"{AGENT_LABEL}.plist"


def desktop_path(home: Path | None = None) -> Path:
    config = os.environ.get("XDG_CONFIG_HOME") if home is None else ""
    return Path(config) / "autostart" / DESKTOP_NAME if config else (home or Path.home()) / ".config" / "autostart" / DESKTOP_NAME


def agent_plist(args: list[str]) -> bytes:
    return plistlib.dumps({"Label": AGENT_LABEL, "ProgramArguments": args, "RunAtLoad": True,
                           "ProcessType": "Interactive"})


def desktop_entry(args: list[str]) -> str:
    exec_line = " ".join(f'"{a}"' if " " in a else a for a in args)
    return ("[Desktop Entry]\nType=Application\nName=IwaraTool\n"
            f"Exec={exec_line}\nX-GNOME-Autostart-enabled=true\n")


def is_enabled(*, home: Path | None = None, platform: str = sys.platform) -> bool:
    if platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                winreg.QueryValueEx(key, VALUE_NAME)
                return True
        except OSError:
            return False
    if platform == "darwin":
        return agent_path(home).is_file()
    return desktop_path(home).is_file()


def _stored(home: Path | None, platform: str) -> str | bytes | None:
    """The entry as the system keeps it (the command line, plist or desktop file); None when absent."""
    if platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                return str(winreg.QueryValueEx(key, VALUE_NAME)[0])
        except OSError:
            return None
    target = agent_path(home) if platform == "darwin" else desktop_path(home)
    try:
        return target.read_bytes() if platform == "darwin" else target.read_text(encoding="utf-8")
    except OSError:
        return None


def _expected(args: list[str], platform: str) -> str | bytes:
    if platform == "win32":
        return _quote_windows(args)
    return agent_plist(args) if platform == "darwin" else desktop_entry(args)


def follow_program(*, home: Path | None = None, platform: str = sys.platform,
                   args: list[str] | None = None) -> bool:
    """Point an existing entry at this program; True when it was rewritten.

    A packaged app calls this at start: an entry left by v1.2 (its own
    ``IwaraTool-Windows-AMD64-<version>.exe``) or by a copy that has moved
    would otherwise start another program, or none, at login.
    """
    if not is_supported(platform):
        return False
    stored = _stored(home, platform)
    if stored is None:
        return False
    args = args or launch_arguments(platform=platform)
    if stored == _expected(args, platform):
        return False
    set_enabled(True, home=home, platform=platform, args=args)
    return True


def set_enabled(enabled: bool, *, home: Path | None = None, platform: str = sys.platform,
                args: list[str] | None = None) -> None:
    """Add or remove the login entry; raises OSError when the system refuses."""
    if not is_supported(platform):
        raise OSError("launch at login is not available on this platform")
    args = args or launch_arguments(platform=platform)
    if platform == "win32":
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            if enabled:
                winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, _quote_windows(args))
            else:
                try:
                    winreg.DeleteValue(key, VALUE_NAME)
                except FileNotFoundError:
                    pass
        return
    target = agent_path(home) if platform == "darwin" else desktop_path(home)
    if not enabled:
        target.unlink(missing_ok=True)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    if platform == "darwin":
        target.write_bytes(agent_plist(args))
    else:
        target.write_text(desktop_entry(args), encoding="utf-8")
