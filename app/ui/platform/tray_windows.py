"""A notification-area (tray) icon for the Flet build on Windows, with ctypes only.

Flet has no tray.  On Windows a tray icon only needs a window and a message
loop on some thread, so this runs a hidden window on its own thread:
``Shell_NotifyIconW`` adds the icon, a click calls ``on_show``, the right-click
menu offers Show and Exit.  Callbacks run on that thread; the caller hands
them over to its event loop.  macOS keeps the Dock (closing minimizes), Linux
has no tray here.
"""
from __future__ import annotations

import sys
import threading
from collections.abc import Callable

from ...logging_setup import get_logger

logger = get_logger(__name__)

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_COMMAND = 0x0111
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_APP = 0x8000
WM_TRAY = WM_APP + 1
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x1, 0x2, 0x4
IMAGE_ICON = 1
LR_LOADFROMFILE, LR_DEFAULTSIZE = 0x10, 0x40
IDI_APPLICATION = 32512
MF_STRING, MF_SEPARATOR = 0x0, 0x800
TPM_RIGHTBUTTON, TPM_RETURNCMD, TPM_NONOTIFY = 0x2, 0x100, 0x80
CMD_SHOW, CMD_EXIT = 1, 2


def is_supported() -> bool:
    return sys.platform == "win32"


class WindowsTray:
    """``start()`` → True when the icon is in the notification area; ``stop()`` removes it."""

    def __init__(self, *, tooltip: str, icon_path: str, show_label: str, exit_label: str,
                 on_show: Callable[[], None], on_exit: Callable[[], None]):
        self.tooltip, self.icon_path = tooltip[:127], icon_path
        self.show_label, self.exit_label = show_label, exit_label
        self.on_show, self.on_exit = on_show, on_exit
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._added = False
        self._hwnd = None

    def start(self, timeout: float = 5.0) -> bool:
        if not is_supported():
            return False
        self._thread = threading.Thread(target=self._run, name="iwaratool-tray", daemon=True)
        self._thread.start()
        self._ready.wait(timeout)
        return self._added

    def stop(self, timeout: float = 5.0) -> None:
        if self._hwnd and is_supported():
            import ctypes

            ctypes.windll.user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    @property
    def running(self) -> bool:
        return self._added and self._thread is not None and self._thread.is_alive()

    # ── the tray thread ──────────────────────────────────────────────────────

    def _run(self) -> None:
        try:
            self._loop()
        except Exception:
            logger.exception("The tray icon stopped")
        finally:
            self._added = False
            self._ready.set()

    def _loop(self) -> None:
        import ctypes
        from ctypes import wintypes

        user32, shell32, kernel32 = ctypes.windll.user32, ctypes.windll.shell32, ctypes.windll.kernel32
        LRESULT = ctypes.c_ssize_t
        WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                        ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                        ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]

        class NOTIFYICONDATAW(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT),
                        ("uFlags", wintypes.UINT), ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
                        ("szTip", wintypes.WCHAR * 128), ("dwState", wintypes.DWORD),
                        ("dwStateMask", wintypes.DWORD), ("szInfo", wintypes.WCHAR * 256),
                        ("uVersion", wintypes.UINT), ("szInfoTitle", wintypes.WCHAR * 64),
                        ("dwInfoFlags", wintypes.DWORD), ("guidItem", ctypes.c_byte * 16),
                        ("hBalloonIcon", wintypes.HICON)]

        user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.DefWindowProcW.restype = LRESULT
        user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                           ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                           wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int,
                                      ctypes.c_int, wintypes.UINT]
        user32.LoadImageW.restype = wintypes.HANDLE
        user32.LoadIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPVOID]
        user32.LoadIconW.restype = wintypes.HICON
        user32.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                          wintypes.HWND, wintypes.LPVOID]
        user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
        user32.CreatePopupMenu.restype = wintypes.HMENU
        user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]

        data = NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)

        def menu(hwnd) -> None:
            popup = user32.CreatePopupMenu()
            user32.AppendMenuW(popup, MF_STRING, CMD_SHOW, self.show_label)
            user32.AppendMenuW(popup, MF_SEPARATOR, 0, None)
            user32.AppendMenuW(popup, MF_STRING, CMD_EXIT, self.exit_label)
            point = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(point))
            user32.SetForegroundWindow(hwnd)  # otherwise the menu does not close on an outside click
            command = user32.TrackPopupMenu(popup, TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY,
                                            point.x, point.y, 0, hwnd, None)
            user32.DestroyMenu(popup)
            if command == CMD_SHOW:
                self._call(self.on_show)
            elif command == CMD_EXIT:
                self._call(self.on_exit)

        def proc(hwnd, message, wparam, lparam):
            if message == WM_TRAY:
                event = lparam & 0xFFFF
                if event in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                    self._call(self.on_show)
                elif event == WM_RBUTTONUP:
                    menu(hwnd)
                return 0
            if message == WM_CLOSE:
                user32.DestroyWindow(hwnd)
                return 0
            if message == WM_DESTROY:
                shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(data))
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(hwnd, message, wparam, lparam)

        self._proc = WNDPROC(proc)  # kept alive for as long as the window exists
        instance = kernel32.GetModuleHandleW(None)
        window_class = WNDCLASSW(lpfnWndProc=self._proc, hInstance=instance,
                                 lpszClassName=f"IwaraToolTray{id(self)}")
        if not user32.RegisterClassW(ctypes.byref(window_class)):
            raise OSError(ctypes.get_last_error() or "RegisterClassW failed")
        # A plain top-level window that is never shown: a message-only window cannot take the foreground,
        # which the tray menu needs to close on an outside click.
        hwnd = user32.CreateWindowExW(0, window_class.lpszClassName, "IwaraTool", 0, 0, 0, 0, 0,
                                      None, None, instance, None)
        if not hwnd:
            raise OSError("CreateWindowExW failed")
        self._hwnd = hwnd
        icon = user32.LoadImageW(None, self.icon_path, IMAGE_ICON, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE) \
            if self.icon_path else None
        data.hWnd, data.uID = hwnd, 1
        data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        data.uCallbackMessage = WM_TRAY
        data.hIcon = icon or user32.LoadIconW(None, ctypes.c_void_p(IDI_APPLICATION))
        data.szTip = self.tooltip
        self._added = bool(shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(data)))
        self._ready.set()
        if not self._added:
            user32.DestroyWindow(hwnd)
            return
        message = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))

    @staticmethod
    def _call(callback: Callable[[], None]) -> None:
        try:
            callback()
        except Exception:
            logger.exception("Tray action failed")
