"""Give idle memory back to the operating system.

Python and Qt keep freed memory in their own pools, so the number Task Manager
shows stays high after a heavy page (a big search, an image post) was closed.
When the window is minimized or hidden to the tray nobody is looking at it, so
the working set can be trimmed; whatever is needed again is paged back in.
"""
from __future__ import annotations

import ctypes
import gc
import sys


def trim_memory() -> bool:
    """Collect garbage and ask the OS to take back unused pages; True if it could."""

    gc.collect()
    try:
        if sys.platform == "win32":
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.GetCurrentProcess.restype = ctypes.c_void_p
            handle = kernel32.GetCurrentProcess()
            psapi = ctypes.WinDLL("psapi", use_last_error=True)
            psapi.EmptyWorkingSet.argtypes = [ctypes.c_void_p]
            return bool(psapi.EmptyWorkingSet(handle))
        if sys.platform.startswith("linux"):
            libc = ctypes.CDLL("libc.so.6")
            return bool(libc.malloc_trim(0))
    except (OSError, AttributeError):
        return False
    return False
